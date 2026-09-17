"""The persistent reasoning loop.

The model owns judgment; the runtime owns reality. This harness orchestrates:

    understand objective -> world state -> uncertainty -> strategy ->
    choose next action -> consequence/risk check -> (runtime executes) ->
    observe -> verify -> update state -> evaluate user goal ->
    continue / replan / finish

It never fabricates consequences and never executes anything itself — actions
go through a caller-supplied `executor` that is grounded in the real runtime
(and the real permissions/governors). It is runtime-agnostic and testable on its
own with a fake executor.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from mcp_vision.reasoning.budget import Budget, measure_progress, should_continue
from mcp_vision.reasoning.consequence import autonomy_allowed, shadow_consequences
from mcp_vision.reasoning.intent import analyze
from mcp_vision.reasoning.memory import ingest_observation
from mcp_vision.reasoning.model_routing import Meta, meta_reason_due, route_tier
from mcp_vision.reasoning.report import compact_state, final_report
from mcp_vision.reasoning.schemas import (
    AgentState,
    Completion,
    ConsequenceLevel,
    Decision,
    Failure,
    ObservationResult,
    Verification,
)
from mcp_vision.reasoning.strategies import classify, recovery_for
from mcp_vision.reasoning.verify import verify_action, verify_state


class Executor(Protocol):
    """Grounded execution surface. Implementations talk to the real runtime."""

    async def act(self, proposed: Any) -> ObservationResult: ...
    async def observe(self) -> dict: ...
    async def close(self) -> None: ...


CompletionEvaluator = Callable[[AgentState, Decision], Completion]


def default_completion(state: AgentState, decision: Decision) -> Completion:
    """What would a competent assistant reasonably consider done?"""
    if not decision.consider_done:
        return Completion(satisfied=False)
    blocking = [u for u in state.uncertainties if u.materially_affects and not u.resolved
                and int(u.blocks_consequence) <= int(state.consequence_level)]
    if blocking:
        return Completion(satisfied=False, blocked=True,
                          unresolved_uncertainty=[u.question for u in blocking],
                          quality_note="Genuine hard unknown remains before a consequential step.")
    quality = "best-effort result from observed evidence" if state.progress.evidence_collected else "answered"
    return Completion(satisfied=True, required_outcome_covered=True,
                      evidence_quality=quality,
                      quality_note=decision.done_summary or "Objective satisfied.")


class ReasoningHarness:
    """Persistent, dynamic reasoning loop for arbitrary user goals."""

    def __init__(self, reasoner: Reasoner, *, executor: Executor | None = None,
                 gate: ConsequenceLevel = ConsequenceLevel.NONE,
                 budget: Budget | None = None,
                 completion_evaluator: CompletionEvaluator | None = None,
                 progress: Callable[[str], None] | None = None,
                 autocancel: Callable[[], None] | None = None) -> None:
        self.reasoner = reasoner
        self.executor = executor
        self.gate = gate
        self.budget = budget or Budget()
        self.completion_evaluator = completion_evaluator or default_completion
        self.progress = progress or (lambda message: None)
        self.autocancel = autocancel
        self.state: AgentState = AgentState()
        self.actions_taken: list[str] = []
        self.actions_not_taken: list[str] = []
        self.action_counter = 0
        self.failures: list[Failure] = []
        self.last_verification: Verification = Verification()
        self.finished = False

    # -- lifecycle ---------------------------------------------------------

    def start(self, request: str, context: object | None = None) -> AgentState:
        """Seed the world state from a (possibly vague) request."""
        self.state = analyze(request, context)
        self.actions_taken = []
        self.actions_not_taken = []
        self.action_counter = 0
        self.failures = []
        self.finished = False
        self.state.memory.working["decision_count"] = 0
        self.state.add_fact("context_mode", getattr(context, "source", None) or "none")
        return self.state

    def update(self, new_request: str, context: object | None = None) -> AgentState:
        """User changed requirements mid-task. Update the constraint, preserve
        still-valid facts/evidence, do not restart from scratch."""
        fresh = analyze(new_request, context)
        s = self.state
        s.raw_request, s.objective, s.mode = fresh.raw_request, fresh.objective, fresh.mode
        s.consequence_level = fresh.consequence_level
        s.constraints_explicit.update(fresh.constraints_explicit)
        s.constraints_inferred.update(fresh.constraints_inferred)
        s.preferences.update(fresh.preferences)
        # Keep observations & verified facts; they remain valid evidence.
        # Drop working assumptions that no longer match the objective.
        s.assumptions = [a for a in s.assumptions if a.statement in fresh.objective or not a.reversible]
        return s

    # -- the loop ----------------------------------------------------------

    async def run(self, request: str, context: object | None = None) -> dict:
        self.start(request, context)
        while not self.finished:
            self._check_cancel()
            decision, meta = await self._decide()
            self._apply_decision(decision)

            if decision.clarification:
                self.actions_not_taken.append("consequential precondition")
                return self._pause("ask", decision.clarification)

            if decision.next is None:
                if decision.consider_done:
                    out = self._attempt_completion(decision)
                    if out is not None:
                        return out
                    continue  # not satisfied and not blocked -> keep going / replan
                if self._should_ask():
                    return self._pause("ask",
                                       "I need a little direction to keep making useful progress.")
                continue

            proposed = decision.next
            # Merge consequences and reason about shadows before any action.
            proposed.consequence = ConsequenceLevel(
                max(int(proposed.consequence), int(self.state.consequence_level)))
            proposed.shadow = shadow_consequences(proposed, self.state.objective)
            if not autonomy_allowed(proposed.consequence, gate=self.gate):
                self.actions_not_taken.append(proposed.action)
                self.progress(f"Pausing before {proposed.action} "
                              f"(consequence: {proposed.consequence.label}).")
                return self._pause("paused",
                                   f"Stopping before {' or '.join(proposed.shadow) or proposed.action}. "
                                   f"Confirm you want this to proceed.")

            self.progress(f"{proposed.action}: {proposed.rationale or proposed.action}")
            receipt = await self._act(proposed)
            self.action_counter += 1
            self.actions_taken.append(proposed.action)

            observation = await self._observe()
            self._verify_and_update(proposed, receipt, observation or {})

            keep_going, _why = should_continue(self.state, budget=self.budget,
                                               action_counter=self.action_counter)
            if not keep_going:
                break
        return self._finish()

    # -- internals ---------------------------------------------------------

    def _check_cancel(self) -> None:
        if self.autocancel is not None:
            self.autocancel()

    async def _decide(self) -> tuple[Decision, Meta]:
        meta_ = Meta(
            ambiguity=bool([u for u in self.state.uncertainties if u.materially_affects
                            and not u.resolved]),
            stalls=self.state.progress.non_progress_steps,
            hard_failures=len([f for f in self.failures if not f.recoverable]),
            consequence=self.state.consequence_level,
            meta_due=meta_reason_due(self.state),
        )
        meta_.notes = [route_tier(meta_)]
        if asyncio.iscoroutinefunction(self.reasoner.decide):
            decision = await self.reasoner.decide(self.state, meta_)
        else:
            decision = await asyncio.to_thread(self.reasoner.decide, self.state, meta_)
        if self.state.meta_checks < (len(self.state.observations) // 6):
            self.state.meta_checks += 1
        return decision, meta_

    def _apply_decision(self, decision: Decision) -> None:
        if decision.strategy and decision.strategy != self.state.strategy.current:
            if self.state.strategy.current:
                self.state.strategy.switch_to(decision.strategy)
            else:
                self.state.strategy.current = decision.strategy
        for a in decision.new_assumptions:
            if a.statement and a.statement not in [x.statement for x in self.state.assumptions]:
                self.state.assumptions.append(a)
        for q in decision.resolved_uncertainties:
            u = self.state.resolved_uncertainty(q)
            if u is not None:
                u.resolved = True
                self.state.progress.uncertainties_resolved += 1
        for f in decision.new_facts:
            self.state.add_fact(f.note, f.value, source=f.source, confidence=f.confidence)

    async def _act(self, proposed: Any) -> ObservationResult:
        if self.executor is None:
            return ObservationResult(ok=True, message="planned only (no executor)")
        return await self.executor.act(proposed)

    async def _observe(self) -> dict | None:
        if self.executor is None:
            return None
        return await self.executor.observe()

    def _verify_and_update(self, proposed: Any, receipt: ObservationResult,
                           observation: dict) -> None:
        ingest_observation(self.state, observation)
        ok_action = verify_action(receipt)
        outcome, detail = verify_state(proposed.expected, observation)
        state_ok = outcome == "success" or (outcome == "new_info" and bool(observation))
        verification = Verification(action=ok_action, state=state_ok,
                                     detail=f"{outcome}: {detail}")
        self.last_verification = verification

        gained = bool(receipt.new_information or outcome in {"success", "new_info", "partial"})
        if not ok_action:
            category = classify(receipt.message or "", receipt_ok=receipt.ok)
            self.failures.append(Failure(category=category,
                                         detail=receipt.message or "action did not execute",
                                         recoverable=category.value not in {"permission_failure"}))
            self.progress(f"recovery: {recovery_for(category)}")

        measure_progress(self.state, gained_info=gained,
                         subgoal_done=state_ok and ok_action,
                         option_found=bool(observation.get("options")),
                         uncertainty_resolved=bool(receipt.new_information))

        if received := [x for x in (receipt.new_information or []) if x]:
            for item in received:
                self.state.add_fact("evidence", str(item), source="observed",
                                    verification="partial", confidence=0.9)

    def _attempt_completion(self, decision: Decision) -> dict | None:
        self.state.completion = self.completion_evaluator(self.state, decision)
        if self.state.completion.satisfied:
            return self._finish()
        if self.state.completion.blocked:
            return self._finish()
        return None

    def _should_ask(self) -> bool:
        # Ask only after we've genuinely stalled with nowhere lower-risk to go.
        return self.state.progress.non_progress_steps >= self.budget.stall_threshold

    def _pause(self, kind: str, message: str) -> dict:
        self.finished = True
        return {"state": kind, "answer": message, "report": final_report(
            self.state, actions_taken=self.actions_taken, actions_not_taken=self.actions_not_taken)}

    def _finish(self) -> dict:
        self.finished = True
        outcome = ("done" if self.state.completion.satisfied else
                   ("blocked" if self.state.completion.blocked else "partial"))
        return {"state": outcome, "answer": self.state.completion.quality_note
                or "Returned the best result available.",
                "report": final_report(self.state, actions_taken=self.actions_taken,
                                       actions_not_taken=self.actions_not_taken)}

    # -- public accessors --------------------------------------------------

    def final_result(self) -> dict:
        """The current task state, exposed in a user-friendly shape."""
        return final_report(self.state, actions_taken=self.actions_taken,
                            actions_not_taken=self.actions_not_taken)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<ReasoningHarness objective={self.state.objective!r} " \
               f"actions={self.action_counter} observations={len(self.state.observations)}>"
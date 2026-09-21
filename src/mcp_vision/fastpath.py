"""Bounded routine-action loop delegated by a System-2 planner."""
from __future__ import annotations

import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mcp_vision.core.models import Policy
from mcp_vision.fast_policy import FastPolicy, PolicyDecision
from mcp_vision.state import ActionCandidate, Operation, UIElement, UIState
from mcp_vision.transactions import TransactionReceipt, TransactionRuntime
from mcp_vision.verification import DEFAULT_VERIFIER, VerificationEngine, VerificationPredicate, VerificationResult


class FastPathStatus(str, Enum):
    VERIFIED = "verified"
    REPLAN = "replan"
    BLOCKED = "blocked"
    UNCERTAIN = "uncertain"
    BUDGET = "budget_exhausted"
    ERROR = "error"


class FastPathTask(BaseModel):
    """Structured handoff: judgment and values come from System-2; execution does not."""

    subgoal: str
    completion: VerificationPredicate
    inputs: dict[str, str | bool | int] = Field(default_factory=dict)


class FastPathConfig(BaseModel):
    max_steps: int = Field(default=8, ge=1, le=30)
    max_retries: int = Field(default=2, ge=0, le=10)
    confidence_threshold: float = Field(default=0.65, ge=0, le=1)
    max_noops: int = Field(default=2, ge=1, le=5)
    max_repeated_action: int = Field(default=2, ge=1, le=5)


class FastPathStep(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    number: int
    state_id: str
    candidate_id: str | None = None
    operation: str | None = None
    target_ref: str | None = None
    policy: str
    confidence: float = 0
    decision_ms: float = 0
    status: str
    reason: str = ""
    transaction: TransactionReceipt | None = None


class FastPathMetrics(BaseModel):
    observations: int = 0
    actions: int = 0
    retries: int = 0
    stale_rejections: int = 0
    noops: int = 0
    policy_ms: float = 0
    total_ms: float = 0
    background_actions: int = 0
    foreground_actions: int = 0


class FastPathResult(BaseModel):
    status: FastPathStatus
    subgoal: str
    reason: str
    verification: VerificationResult | None = None
    final_state: UIState | None = None
    steps: tuple[FastPathStep, ...] = ()
    metrics: FastPathMetrics
    subgoal_complete: bool = False
    task_complete: bool = False


class FastPath:
    def __init__(self, runtime: TransactionRuntime, policy: FastPolicy, *,
                 verifier: VerificationEngine | None = None, config: FastPathConfig | None = None):
        self.runtime = runtime
        self.policy = policy
        self.verifier = verifier or DEFAULT_VERIFIER
        self.config = config or FastPathConfig()

    async def run(self, task: FastPathTask) -> FastPathResult:
        started = time.perf_counter()
        metrics = FastPathMetrics()
        steps: list[FastPathStep] = []
        repeats: dict[tuple[Any, ...], int] = {}
        retries = noops = 0
        self.runtime.record_event("fastpath_start", subgoal=task.subgoal,
                                  completion=task.completion.model_dump(mode="json"))
        initial = state = await self.runtime.observe()
        metrics.observations += 1
        verification = self.verifier.verify(state, task.completion, before=initial)
        if verification.passed:
            return self._finish(FastPathStatus.VERIFIED, task, "Completion already satisfied.",
                                state, verification, steps, metrics, started)

        for number in range(1, self.config.max_steps + 1):
            candidates = self._eligible_candidates(task, state)
            decision_started = time.perf_counter()
            try:
                decision = await self.policy.choose(task.subgoal, state, candidates)
            except Exception as exc:
                return self._finish(FastPathStatus.REPLAN, task,
                                    f"Fast policy rejected its bounded input: {type(exc).__name__}.",
                                    state, verification, steps, metrics, started)
            decision_ms = (time.perf_counter() - decision_started) * 1000
            metrics.policy_ms += decision_ms
            if decision.needs_system2 or not decision.candidate_id:
                steps.append(self._step(number, state, decision, decision_ms, "replan"))
                return self._finish(FastPathStatus.REPLAN, task, decision.reason or "Policy requested System-2.",
                                    state, verification, steps, metrics, started)
            if decision.confidence < self.config.confidence_threshold:
                steps.append(self._step(number, state, decision, decision_ms, "uncertain",
                                        "Policy confidence is below threshold."))
                return self._finish(FastPathStatus.UNCERTAIN, task, "Low-confidence bounded decision.",
                                    state, verification, steps, metrics, started)
            candidate = state.candidate(decision.candidate_id)
            if candidate is None:
                return self._finish(FastPathStatus.ERROR, task, "Policy returned an invalid candidate.",
                                    state, verification, steps, metrics, started)
            if candidate.risk is Policy.RESTRICTED_ACTION:
                steps.append(self._step(number, state, decision, decision_ms, "blocked",
                                        "Consequential action requires System-2 and operator approval."))
                return self._finish(FastPathStatus.BLOCKED, task, "Safety escalation required.",
                                    state, verification, steps, metrics, started)
            arguments = self._arguments(task, state, candidate)
            signature = (state.content_hash, candidate.operation.value, candidate.target_ref,
                         tuple(sorted(arguments.items())))
            repeats[signature] = repeats.get(signature, 0) + 1
            if repeats[signature] > self.config.max_repeated_action:
                return self._finish(FastPathStatus.REPLAN, task, "Repeated-action loop detected.",
                                    state, verification, steps, metrics, started)

            receipt = await self.runtime.execute(state.state_id, candidate.id, **arguments)
            metrics.actions += int(receipt.action.executed is not False)
            if receipt.action.executed is not False:
                if receipt.action.evidence.get("background") is True:
                    metrics.background_actions += 1
                elif receipt.action.evidence.get("background") is False:
                    metrics.foreground_actions += 1
            step = self._step(number, state, decision, decision_ms, receipt.status,
                              receipt.action.message, receipt)
            steps.append(step)
            if receipt.status == "stale":
                metrics.stale_rejections += 1
                retries += 1
                metrics.retries = retries
                if retries > self.config.max_retries:
                    return self._finish(FastPathStatus.REPLAN, task, "Stale-state retry budget exhausted.",
                                        state, verification, steps, metrics, started)
                state = await self.runtime.observe()
                metrics.observations += 1
                continue
            if receipt.status == "blocked":
                return self._finish(FastPathStatus.BLOCKED, task, receipt.action.message,
                                    state, verification, steps, metrics, started)
            if receipt.status == "error" or receipt.successor_state is None:
                return self._finish(FastPathStatus.ERROR, task, receipt.action.message,
                                    state, verification, steps, metrics, started)
            state = receipt.successor_state
            metrics.observations += 1
            if not receipt.diff or not receipt.diff.changed:
                noops += 1
                metrics.noops = noops
                if noops >= self.config.max_noops:
                    return self._finish(FastPathStatus.REPLAN, task, "No-op limit reached.",
                                        state, verification, steps, metrics, started)
            else:
                noops = 0
            verification = self.verifier.verify(state, task.completion, before=initial)
            if verification.passed:
                return self._finish(FastPathStatus.VERIFIED, task, "Completion predicate verified.",
                                    state, verification, steps, metrics, started)

        return self._finish(FastPathStatus.BUDGET, task, "FastPath step budget exhausted.",
                            state, verification, steps, metrics, started)

    def _eligible_candidates(self, task: FastPathTask, state: UIState) -> tuple[ActionCandidate, ...]:
        candidates = []
        for candidate in state.candidates:
            if candidate.risk is Policy.RESTRICTED_ACTION:
                continue
            if candidate.argument in {"text", "value", "checked"}:
                target = state.element(candidate.target_ref or "")
                if target is None or self._input_for(task, target) is None:
                    continue
                desired = self._input_for(task, target)
                if candidate.operation is Operation.TYPE and str(desired).casefold() == target.value.casefold():
                    continue
                if candidate.operation is Operation.SET_CHECKED and bool(desired) is target.checked:
                    continue
            candidates.append(candidate)
        return tuple(candidates)

    @staticmethod
    def _input_for(task: FastPathTask, element: UIElement) -> str | bool | int | None:
        exact = task.inputs.get(element.name)
        if exact is not None:
            return exact
        matches = [value for key, value in task.inputs.items()
                   if key.casefold() in element.name.casefold() or element.name.casefold() in key.casefold()]
        return matches[0] if len(matches) == 1 else None

    def _arguments(self, task: FastPathTask, state: UIState, candidate: ActionCandidate) -> dict[str, Any]:
        if candidate.operation is Operation.SCROLL:
            return {"delta_y": 600}
        if candidate.operation is Operation.WAIT:
            return {"milliseconds": 100}
        if candidate.argument:
            target = state.element(candidate.target_ref or "")
            desired = self._input_for(task, target) if target else None
            return {candidate.argument: desired}
        return {}

    @staticmethod
    def _step(number: int, state: UIState, decision: PolicyDecision, decision_ms: float,
              status: str, reason: str = "", transaction: TransactionReceipt | None = None) -> FastPathStep:
        candidate = state.candidate(decision.candidate_id or "")
        return FastPathStep(number=number, state_id=state.state_id, candidate_id=decision.candidate_id,
                            operation=candidate.operation.value if candidate else None,
                            target_ref=candidate.target_ref if candidate else None,
                            policy=decision.provider, confidence=decision.confidence,
                            decision_ms=decision_ms, status=status, reason=reason, transaction=transaction)

    def _finish(self, status: FastPathStatus, task: FastPathTask, reason: str, state: UIState,
                verification: VerificationResult | None, steps: list[FastPathStep],
                metrics: FastPathMetrics, started: float) -> FastPathResult:
        metrics.total_ms = (time.perf_counter() - started) * 1000
        result = FastPathResult(status=status, subgoal=task.subgoal, reason=reason,
                                verification=verification, final_state=state, steps=tuple(steps), metrics=metrics,
                                subgoal_complete=status is FastPathStatus.VERIFIED,
                                task_complete=False)
        self.runtime.record_event("fastpath_end", subgoal=task.subgoal, status=status.value,
                                  reason=reason, steps=len(steps), metrics=metrics.model_dump(),
                                  verified=bool(verification and verification.passed))
        return result

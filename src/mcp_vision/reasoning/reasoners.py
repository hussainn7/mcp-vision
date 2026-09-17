"""Reasoners: produce a compact structured Decision for one loop pass.

The harness owns the loop; the reasoner owns judgment. Nothing here executes an
action. Two implementations:

- ModelReasoner: token-efficient chat call (the real intelligence layer).
- HeuristicReasoner: offline fallback / smoke path. It is deliberately simple
  and honest about it — useful for tests and no-model environments, never a
  substitute for real reasoning on consequential work.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Protocol

from mcp_vision.reasoning.report import compact_state
from mcp_vision.reasoning.schemas import (
    AgentState,
    Assumption,
    ConsequenceLevel,
    Decision,
    ExpectedOutcome,
    ProposedAction,
)


class Reasoner(Protocol):
    def decide(self, state: AgentState, meta: Any) -> Decision: ...


def _find_flight(state: AgentState) -> bool:
    return bool(re.search(r"\b(flights?|fly|flying|round.?trip|one.?way)\b",
                          state.objective.lower()))


def _consequential(state: AgentState) -> bool:
    return int(state.consequence_level) >= int(ConsequenceLevel.SIGNIFICANT)


class HeuristicReasoner:
    """Conservative fallback. Gathers a few observations, then considers done.
    Makes reversible assumptions for research, and asks before acting on
    consequential goals when a precondition is missing."""

    def __init__(self, gather_target: int = 3) -> None:
        self.gather_target = gather_target

    def _count(self, state: AgentState) -> int:
        return int(state.memory.working.setdefault("decision_count", 0))

    def decide(self, state: AgentState, meta: Any = None) -> Decision:
        n = self._count(state)
        state.memory.working["decision_count"] = n + 1

        goal = (state.objective or "").lower()
        evidence = len(state.observations)

        if _consequential(state) and n == 0:
            missing = self._missing_precondition(state)
            if missing:
                return Decision(clarification=missing,
                                meta="confirm one precondition before consequential action")

        if n == 0:
            return self._first_action(state, goal)

        # Stall -> switch strategy before blindly repeating.
        if state.progress.non_progress_steps >= 2 and state.strategy.alternatives:
            state.strategy.switch_to(state.strategy.alternatives[0],
                                     "stalled; trying an alternative source")
            return Decision(strategy=state.strategy.current,
                            next=ProposedAction(
                                action="search", params={"query": goal},
                                expected=ExpectedOutcome(action="search", success_signal="results")),
                            meta="switched strategy after stall")

        if evidence >= self.gather_target:
            return Decision(
                strategy=state.strategy.current,
                consider_done=True,
                done_summary=f"Gathered {evidence} observation(s) toward '{state.objective}'.",
                meta="enough reversible evidence for a best-effort result")

        return Decision(
            strategy=state.strategy.current,
            next=ProposedAction(
                action="search", params={"query": goal},
                expected=ExpectedOutcome(action="search", success_signal="results"),
                consequence=ConsequenceLevel.NONE, reversible=True,
                rationale="keep lowering uncertainty about the objective"),
            meta="continue gathering")

    def _missing_precondition(self, state: AgentState) -> str:
        text = (state.raw_request + " " + " ".join(str(v) for v in state.entities.values())).lower()
        if re.search(r"\b(send|email|forward|get .* to)\b", text) and not re.search(r"\b@|alex\b", text):
            return "Who should I send it to?"
        if re.search(r"\b(apply|submit|fill)\b", text) and not re.search(r"\b(resume|file|attach)\b", text):
            return "Which file or details should I use?"
        return ""

    def _first_action(self, state: AgentState, goal: str) -> Decision:
        if _find_flight(state):
            state.add_assumption(
                "dates flexible; one traveler; economy class",
                basis="reasonable defaults for exploratory flight research",
                confidence=0.5, reversible=True, verify_before=ConsequenceLevel.SIGNIFICANT)
            return Decision(
                strategy="compare flight options",
                next=ProposedAction(
                    action="search", params={"query": goal, "kind": "flights"},
                    expected=ExpectedOutcome(action="search", expected_effect="flight options",
                                             success_signal="results"),
                    consequence=ConsequenceLevel.NONE, reversible=True,
                    rationale="research only; dates assumed flexible"),
                meta="research path; never purchase without confirmation")
        if re.search(r"\b(clean|organize|sort|tidy)\b", goal):
            state.add_assumption(
                "goal is reversible organization; leave ambiguous files alone",
                basis="organizing a folder should be reversible and cautious",
                reversible=True, verify_before=ConsequenceLevel.SIGNIFICANT)
            return Decision(
                strategy="inspect then group obvious items",
                next=ProposedAction(
                    action="list", params={"scope": "current folder"},
                    expected=ExpectedOutcome(action="list", success_signal="items"),
                    consequence=ConsequenceLevel.NONE, reversible=True,
                    rationale="look before grouping"),
                meta="inspect first")
        if re.search(r"\b(fix|why isn'?t|why is|diagnos|not working|broken|error)\b", goal):
            return Decision(
                strategy="hypothesis-test the failure",
                next=ProposedAction(
                    action="inspect", params={"scope": "current environment"},
                    expected=ExpectedOutcome(action="inspect", success_signal="error detail"),
                    consequence=ConsequenceLevel.NONE, reversible=True,
                    rationale="reproduce/observe before forming hypotheses"),
                meta="diagnose the environment")
        if _consequential(state):
            # Consequential but precondition present: prepare, don't commit.
            return Decision(
                strategy="prepare without committing",
                next=ProposedAction(
                    action="draft", params={"topic": goal},
                    expected=ExpectedOutcome(action="draft", expected_effect="reviewable draft",
                                             success_signal="draft ready"),
                    consequence=ConsequenceLevel.MINOR, reversible=True,
                    rationale="prepare the consequential step for confirmation"),
                meta="prepare then pause for the user")
        return Decision(
            strategy="search/research",
            next=ProposedAction(
                action="search", params={"query": goal},
                expected=ExpectedOutcome(action="search", success_signal="results"),
                consequence=ConsequenceLevel.NONE, reversible=True,
                rationale="start research at the objective"),
            meta="research path")


class ModelReasoner:
    """Token-efficient chat-backed reasoner for the real intelligence layer."""

    _SYSTEM = (
        "You are the reasoner inside a general autonomy harness. Your job is one "
        "decision per call: the next best action, or whether to stop or ask. "
        "You own judgment; a separate runtime owns real execution and safety. "
        "Prefer reversible, low-risk actions and reversible assumptions. Research "
        "hard, but never authorise sending, submitting, booking, buying, deleting, "
        "or identity-sensitive actions yourself. Ask the user only late, after "
        "assuming is cheaper and safe.\n"
        "Reply ONLY with JSON for a Decision object with these keys: "
        "strategy (string); next (object, optional) with keys action (one of "
        "search inspect list compare draft fill click send submit buy delete), "
        "params (object), expected_effect, success_signal, failure_signal "
        "(strings), consequence (int 0-4), reversible (bool), rationale (string), "
        "shadow (array of strings); clarification (string, empty when not asking); "
        "consider_done (bool); done_summary (string); new_assumptions (array of "
        "strings); resolved_uncertainties (array of strings); meta (string).\n"
        "consequence levels: 0 read/search, 1 minor, 2 recoverable, 3 significant "
        "(send/submit/buy/delete), 4 critical. Keep every field short."
    )

    def __init__(self, chat: Callable | None = None, provider: str | None = None) -> None:
        from backends import get_chat
        from mcp_vision.providers import resolve_provider
        self.chat = chat or get_chat(resolve_provider(provider))

    def decide(self, state: AgentState, meta: Any = None) -> Decision:
        payload = compact_state(state)
        payload["routing"] = meta.__dict__ if hasattr(meta, "__dict__") else str(meta)
        result = self.chat([{"role": "system", "content": self._SYSTEM},
                            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                           tools=None)
        raw = (result.get("content") or "").strip()
        return parse_decision(raw)


def parse_decision(raw: str) -> Decision:
    """Robustly parse a Decision from model text (may wrap JSON in prose)."""
    decoder = json.JSONDecoder()
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    candidates = [text] + [text[i:] for i, c in enumerate(text) if c == "{"]
    for candidate in candidates:
        try:
            obj, _ = decoder.raw_decode(candidate.lstrip())
            return _coerce(json.loads(json.dumps(obj)))
        except (json.JSONDecodeError, ValueError, KeyError):
            continue
    raise ValueError("The reasoner did not return a valid Decision.")


def _coerce(obj: dict) -> Decision:
    nxt = obj.get("next") or {}
    proposed = None
    if nxt:
        proposed = ProposedAction(
            action=nxt.get("action", ""),
            params=nxt.get("params") or {},
            expected=ExpectedOutcome(
                action=nxt.get("action", ""),
                expected_effect=nxt.get("expected_effect", ""),
                success_signal=nxt.get("success_signal", ""),
                failure_signal=nxt.get("failure_signal", "")),
            consequence=ConsequenceLevel(int(nxt.get("consequence", 0))),
            reversible=bool(nxt.get("reversible", True)),
            rationale=nxt.get("rationale", ""),
            shadow=nxt.get("shadow") or [])
    assumptions = []
    for a in obj.get("new_assumptions") or []:
        if isinstance(a, dict):
            assumptions.append(Assumption(statement=str(a.get("statement", a.get("value", "")))))
        else:
            assumptions.append(Assumption(statement=str(a)))
    return Decision(
        strategy=obj.get("strategy", ""),
        next=proposed,
        clarification=obj.get("clarification", ""),
        consider_done=bool(obj.get("consider_done")),
        done_summary=obj.get("done_summary", ""),
        new_assumptions=assumptions,
        resolved_uncertainties=[str(x) for x in (obj.get("resolved_uncertainties") or [])],
        meta=obj.get("meta", ""))
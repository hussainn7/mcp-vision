"""Provider-neutral bounded decision policies.

Policies may select a supplied candidate ID.  They cannot create actions and
they never execute them.  Consequential candidates always escalate to System-2.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
from typing import Any, Protocol, runtime_checkable
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field

from mcp_vision.core.models import Policy
from mcp_vision.state import ActionCandidate, Operation, UIState


class PolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    state_id: str
    candidate_id: str | None = None
    disposition: str = "abstain"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_system2: bool = True
    reason: str = ""
    provider: str = "disabled"
    latency_ms: float = 0.0


@runtime_checkable
class FastPolicy(Protocol):
    async def choose(self, goal: str, state: UIState,
                     candidates: tuple[ActionCandidate, ...] | None = None) -> PolicyDecision: ...


def validate_decision(decision: PolicyDecision, state: UIState,
                      candidates: tuple[ActionCandidate, ...]) -> PolicyDecision:
    if decision.state_id != state.state_id:
        raise ValueError("policy decision belongs to a different state")
    if decision.candidate_id is None:
        return decision
    candidate = next((item for item in candidates if item.id == decision.candidate_id), None)
    if candidate is None or candidate.state_id != state.state_id:
        raise ValueError("policy selected an unknown or stale candidate")
    if candidate.risk is Policy.RESTRICTED_ACTION:
        return decision.model_copy(update={"candidate_id": None, "disposition": "replan",
                                           "needs_system2": True,
                                           "reason": "Consequential candidates require System-2 and runtime approval."})
    return decision


class DisabledPolicy:
    async def choose(self, goal: str, state: UIState,
                     candidates: tuple[ActionCandidate, ...] | None = None) -> PolicyDecision:
        return PolicyDecision(state_id=state.state_id, reason="Fast policy is disabled.")


class MockPolicy:
    def __init__(self, candidate_id: str | None, confidence: float = 1.0):
        self.candidate_id = candidate_id
        self.confidence = confidence

    async def choose(self, goal: str, state: UIState,
                     candidates: tuple[ActionCandidate, ...] | None = None) -> PolicyDecision:
        choices = candidates or state.candidates
        decision = PolicyDecision(state_id=state.state_id, candidate_id=self.candidate_id,
                                  disposition="candidate" if self.candidate_id else "abstain",
                                  confidence=self.confidence, needs_system2=self.candidate_id is None,
                                  provider="mock")
        return validate_decision(decision, state, choices)


class RulePolicy:
    """Conservative zero-network policy for obvious, non-consequential presses."""

    async def choose(self, goal: str, state: UIState,
                     candidates: tuple[ActionCandidate, ...] | None = None) -> PolicyDecision:
        choices = candidates or state.candidates
        words = {word.strip(".,!?()[]{}\"").lower() for word in goal.split() if len(word) > 2}
        ranked: list[tuple[int, ActionCandidate]] = []
        for candidate in choices:
            if candidate.operation is not Operation.PRESS or candidate.risk is Policy.RESTRICTED_ACTION:
                continue
            target = state.element(candidate.target_ref or "")
            label_words = {word.lower() for word in (target.name if target else "").split()}
            score = len(words & label_words)
            if score:
                ranked.append((score, candidate))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        if not ranked or (len(ranked) > 1 and ranked[0][0] == ranked[1][0]):
            return PolicyDecision(state_id=state.state_id, disposition="replan", needs_system2=True,
                                  reason="No unique routine action matched the goal.", provider="rules")
        candidate = ranked[0][1]
        return PolicyDecision(state_id=state.state_id, candidate_id=candidate.id,
                              disposition="candidate", confidence=min(0.95, 0.65 + ranked[0][0] * 0.1),
                              needs_system2=False, reason="Unique semantic label match.", provider="rules")


class JevPolicy:
    """Optional TypeSafe/Jev adapter; credentials are read only at call time."""

    def __init__(self, *, api_key: str | None = None, endpoint: str | None = None,
                 model: str | None = None):
        self.api_key = api_key
        self.endpoint = endpoint or os.environ.get("TYPESAFE_ENDPOINT", "https://api.typesafe.ai/v1/systemone")
        self.model = model or os.environ.get("TYPESAFE_MODEL", "jev-latest")

    async def choose(self, goal: str, state: UIState,
                     candidates: tuple[ActionCandidate, ...] | None = None) -> PolicyDecision:
        import time
        started = time.perf_counter()
        choices = candidates or state.candidates
        safe = tuple(item for item in choices if item.risk is not Policy.RESTRICTED_ACTION)
        key = self.api_key or os.environ.get("TYPESAFE_API_KEY")
        if not key:
            return PolicyDecision(state_id=state.state_id, disposition="replan", needs_system2=True,
                                  reason="TYPESAFE_API_KEY is not configured.", provider="jev")
        criteria = {item.id: item.label for item in safe}
        criteria.update(REOBSERVE="The observation may be stale.", REPLAN="System-2 judgment is required.")
        body = {"model": self.model, "state": {"goal": goal, "url": state.url, "title": state.title,
                "text": state.text[:6000]}, "questions": {"next_action": {"type": "choice", "criteria": criteria}}}

        def request() -> dict[str, Any]:
            raw = json.dumps(body).encode()
            req = Request(self.endpoint, data=raw, method="POST", headers={
                "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            with urlopen(req, timeout=25) as response:
                return json.loads(response.read())

        try:
            result = await asyncio.to_thread(request)
            answer = result["answers"]["next_action"]
            probabilities = answer["probabilities"]
            confidence = answer.get("confidence", probabilities.get(answer.get("choice")))
            if (answer.get("choice") not in criteria or set(probabilities) != set(criteria)
                    or not all(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1
                               for value in probabilities.values())
                    or type(confidence) not in (int, float) or not math.isfinite(confidence)
                    or not 0 <= confidence <= 1
                    or probabilities[answer["choice"]] < max(probabilities.values()) - 1e-6
                    or abs(sum(probabilities.values()) - 1) >= 0.02):
                raise ValueError("invalid bounded-choice response")
            selected = answer["choice"]
            decision = PolicyDecision(
                state_id=state.state_id, candidate_id=selected if selected in {c.id for c in safe} else None,
                disposition="candidate" if selected in {c.id for c in safe} else selected.lower(),
                confidence=float(confidence),
                needs_system2=selected == "REPLAN", reason="Bounded Jev choice.", provider="jev",
                latency_ms=(time.perf_counter() - started) * 1000,
            )
            return validate_decision(decision, state, safe)
        except Exception as exc:
            return PolicyDecision(state_id=state.state_id, disposition="replan", needs_system2=True,
                                  reason=f"Jev unavailable or invalid: {type(exc).__name__}", provider="jev",
                                  latency_ms=(time.perf_counter() - started) * 1000)


def create_fast_policy(name: str) -> FastPolicy:
    normalized = name.strip().lower()
    if normalized == "jev":
        return JevPolicy()
    if normalized in {"rules", "local"}:
        return RulePolicy()
    if normalized in {"disabled", "system2"}:
        return DisabledPolicy()
    raise ValueError("fast_policy must be jev, rules, local, system2, or disabled")

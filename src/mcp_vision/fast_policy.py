"""Provider-neutral bounded decision policies.

Policies may select a supplied candidate ID.  They cannot create actions and
they never execute them.  Consequential candidates always escalate to System-2.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    operation: str | None = None
    progress: float | None = Field(default=None, ge=0.0, le=1.0)
    stale_likelihood: float | None = Field(default=None, ge=0.0, le=1.0)
    expected_success: float | None = Field(default=None, ge=0.0, le=1.0)
    probabilities: dict[str, float] = Field(default_factory=dict)
    provider_call: str = "not_attempted"
    fallback: str | None = None


class _JevSettings(BaseSettings):
    """Small, provider-local config. Pydantic reads .env without exporting secrets."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    typesafe_api_key: str | None = None
    typesafe_endpoint: str = "https://api.typesafe.ai/v1/systemone"
    typesafe_model: str = "jev-latest"


def _jev_settings(load_env: bool = True) -> _JevSettings:
    if not load_env:
        return _JevSettings(_env_file=None)
    candidates = (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env")
    env_file = next((path for path in candidates if path.is_file()), None)
    return _JevSettings(_env_file=env_file)


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
    """Conservative zero-network policy for obvious routine operations and targets."""

    async def choose(self, goal: str, state: UIState,
                     candidates: tuple[ActionCandidate, ...] | None = None) -> PolicyDecision:
        choices = candidates or state.candidates
        import re

        words = {word for word in re.findall(r"[a-z0-9]+", goal.casefold()) if len(word) > 1}
        operation_words = {
            Operation.PRESS: {"press", "click", "open", "choose", "select", "set", "change"},
            Operation.TYPE: {"type", "enter", "fill", "set", "change", "write"},
            Operation.SELECT: {"select", "choose", "set", "change"},
            Operation.SET_CHECKED: {"check", "uncheck", "enable", "disable", "toggle", "set"},
            Operation.SCROLL: {"scroll", "below", "down", "up"},
            Operation.WAIT: {"wait", "loading", "settle"},
        }
        ranked: list[tuple[int, ActionCandidate]] = []
        for candidate in choices:
            if candidate.risk is Policy.RESTRICTED_ACTION or candidate.operation in {
                Operation.REPLAN, Operation.REOBSERVE,
            }:
                continue
            target = state.element(candidate.target_ref or "")
            label_words = set(re.findall(r"[a-z0-9]+", (target.name if target else candidate.label).casefold()))
            overlap = len(words & label_words)
            operation_match = bool(words & operation_words.get(candidate.operation, set()))
            score = overlap * 3 + int(operation_match)
            if candidate.operation in {Operation.SCROLL, Operation.WAIT} and not operation_match:
                score = 0
            if score:
                ranked.append((score, candidate))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        if not ranked or (len(ranked) > 1 and ranked[0][0] == ranked[1][0]):
            return PolicyDecision(state_id=state.state_id, disposition="replan", needs_system2=True,
                                  reason="No unique routine action matched the goal.", provider="rules")
        candidate = ranked[0][1]
        return PolicyDecision(state_id=state.state_id, candidate_id=candidate.id,
                              disposition="candidate", confidence=min(0.95, 0.65 + ranked[0][0] * 0.1),
                              needs_system2=False, reason="Unique semantic operation and label match.",
                              provider="rules", operation=candidate.operation.value)


class JevPolicy:
    """Optional TypeSafe/Jev adapter; credentials are read only at call time."""

    def __init__(self, *, api_key: str | None = None, endpoint: str | None = None,
                 model: str | None = None, load_env: bool = True):
        settings = _jev_settings(load_env)
        self.api_key = api_key or settings.typesafe_api_key
        self.endpoint = endpoint or settings.typesafe_endpoint
        self.model = model or settings.typesafe_model

    async def choose(self, goal: str, state: UIState,
                     candidates: tuple[ActionCandidate, ...] | None = None) -> PolicyDecision:
        import time
        started = time.perf_counter()
        choices = candidates or state.candidates
        safe = tuple(item for item in choices if item.risk is not Policy.RESTRICTED_ACTION)
        key = self.api_key or os.environ.get("TYPESAFE_API_KEY")
        if not key:
            return PolicyDecision(state_id=state.state_id, disposition="replan", needs_system2=True,
                                  reason="TYPESAFE_API_KEY is not configured.", provider="jev",
                                  fallback="system2")
        groups: dict[str, list[ActionCandidate]] = {}
        for item in safe:
            groups.setdefault(item.operation.value, []).append(item)
        operation_criteria = {
            operation: f"Execute one supplied {operation} candidate."
            for operation in groups
        }
        questions: dict[str, Any] = {
            "operation": {"type": "choice", "criteria": operation_criteria,
                          "instructions": {"goal": goal, "rule": "Choose one bounded next operation."}},
            "progress": {"type": "choice", "criteria": {
                "0": "No visible progress", "25": "Started", "50": "Partly complete",
                "75": "Mostly complete", "100": "Visibly complete"}},
            "needs_system2": {"type": "choice", "criteria": {
                "no": "Routine bounded action is sufficient", "yes": "Judgment or authorization is required"}},
            "stale": {"type": "choice", "criteria": {
                "no": "Observation appears current", "yes": "Reobserve before any action"}},
            "expected_success": {"type": "choice", "criteria": {
                "no": "Selected action is unlikely to progress", "yes": "Selected action should progress"}},
        }
        for operation, items in groups.items():
            questions[f"{operation}_target"] = {
                "type": "choice",
                "criteria": {item.id: self._serialize_candidate(item, state) for item in items},
                "instructions": {"goal": goal, "operation": operation,
                                 "rule": "Assume this operation was chosen; select only its target."},
            }
        body = {"model": self.model, "state": {"goal": goal, "state_id": state.state_id,
                "url": state.url, "title": state.title, "text": state.text[:6000]}, "questions": questions}

        def request() -> dict[str, Any]:
            raw = json.dumps(body).encode()
            req = Request(self.endpoint, data=raw, method="POST", headers={
                "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            with urlopen(req, timeout=25) as response:
                return json.loads(response.read())

        try:
            result = await asyncio.to_thread(request)
            answers = result["answers"]
            operation_answer = self._validated_answer(answers.get("operation", {}), set(operation_criteria))
            operation = operation_answer["choice"]
            target_criteria = {item.id for item in groups[operation]}
            target_answer = self._validated_answer(answers.get(f"{operation}_target", {}), target_criteria)
            selected = target_answer["choice"]
            needs_system2 = self._choice_probability(answers.get("needs_system2"), "yes")
            stale = self._choice_probability(answers.get("stale"), "yes")
            expected_success = self._choice_probability(answers.get("expected_success"), "yes")
            progress_answer = answers.get("progress") or {}
            progress = (float(progress_answer.get("choice")) / 100
                        if str(progress_answer.get("choice", "")).isdigit() else None)
            confidence = min(float(operation_answer["confidence"]), float(target_answer["confidence"]))
            reobserve = next((item for item in safe if item.operation is Operation.REOBSERVE), None)
            if stale is not None and stale >= 0.5 and reobserve:
                selected, operation = reobserve.id, Operation.REOBSERVE.value
            decision = PolicyDecision(
                state_id=state.state_id, candidate_id=None if needs_system2 is not None and needs_system2 >= 0.5 else selected,
                disposition="replan" if needs_system2 is not None and needs_system2 >= 0.5 else "candidate",
                confidence=confidence, needs_system2=bool(needs_system2 is not None and needs_system2 >= 0.5),
                reason="Parallel bounded Jev operation/target choice.", provider="jev", operation=operation,
                latency_ms=(time.perf_counter() - started) * 1000,
                progress=progress, stale_likelihood=stale, expected_success=expected_success,
                probabilities=dict(target_answer["probabilities"]),
                provider_call="successful",
                fallback="system2" if needs_system2 is not None and needs_system2 >= 0.5 else None,
            )
            return validate_decision(decision, state, safe)
        except Exception as exc:
            failure = f"HTTP {exc.code}" if isinstance(exc, HTTPError) else type(exc).__name__
            return PolicyDecision(state_id=state.state_id, disposition="replan", needs_system2=True,
                                  reason=f"Jev unavailable or invalid: {failure}", provider="jev",
                                  latency_ms=(time.perf_counter() - started) * 1000,
                                  provider_call="failed", fallback="system2")

    @staticmethod
    def _serialize_candidate(candidate: ActionCandidate, state: UIState) -> dict[str, Any]:
        element = state.element(candidate.target_ref or "")
        return {
            "action": candidate.label,
            "target": candidate.target_ref,
            "role": element.role if element else None,
            "name": element.name if element else None,
            "value": element.value if element else None,
            "checked": element.checked if element else None,
            "bounds": element.bounds.model_dump() if element else None,
            "capabilities": [cap.value for cap in element.capabilities] if element else [],
        }

    @staticmethod
    def _validated_answer(answer: dict[str, Any], choices: set[str]) -> dict[str, Any]:
        probabilities = answer.get("probabilities")
        selected = answer.get("choice")
        confidence = answer.get("confidence", probabilities.get(selected) if isinstance(probabilities, dict) else None)
        valid = (
            selected in choices and isinstance(probabilities, dict) and set(probabilities) == choices
            and all(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1
                    for value in probabilities.values())
            and type(confidence) in (int, float) and math.isfinite(confidence) and 0 <= confidence <= 1
            and probabilities[selected] >= max(probabilities.values()) - 1e-6
            and abs(sum(probabilities.values()) - 1) < 0.02
        )
        if not valid:
            raise ValueError("invalid bounded-choice response")
        return {**answer, "confidence": confidence}

    @classmethod
    def _choice_probability(cls, answer: dict[str, Any] | None, choice: str) -> float | None:
        if not answer:
            return None
        try:
            valid = cls._validated_answer(answer, {"yes", "no"})
            return float(valid["probabilities"][choice])
        except ValueError:
            return None


def jev_status() -> dict[str, Any]:
    """Expose configuration presence without pretending it is a health check."""
    settings = _jev_settings()
    configured = bool(settings.typesafe_api_key)
    return {"configured": configured, "verified": False, "model": settings.typesafe_model,
            "status": "configured_unverified" if configured else "not_configured"}


def create_fast_policy(name: str) -> FastPolicy:
    normalized = name.strip().lower()
    if normalized == "jev":
        return JevPolicy()
    if normalized in {"rules", "local"}:
        return RulePolicy()
    if normalized in {"disabled", "system2"}:
        return DisabledPolicy()
    raise ValueError("fast_policy must be jev, rules, local, system2, or disabled")

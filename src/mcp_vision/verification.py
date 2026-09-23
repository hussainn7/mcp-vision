"""Reusable semantic verification over immutable UI states.

Verification returns the evidence used for a decision.  It never promotes a
primitive action receipt into task completion.
"""
from __future__ import annotations

import re
import asyncio
import time
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, computed_field

from mcp_vision.state import UIElement, UIState


PredicateKind = Literal[
    "element_exists", "element_missing", "text_equals", "text_contains",
    "text_absent", "value_equals", "checked_equals", "url_matches", "url_equals",
    "window_exists", "window_closed",
    "attribute_equals", "state_changed", "custom",
]


class StabilityPolicy(BaseModel):
    consecutive_samples: int = Field(default=2, ge=1, le=5)
    cadence_ms: int = Field(default=100, ge=1, le=10_000)
    timeout_ms: int = Field(default=3_000, ge=0, le=10_000)


class VerificationPredicate(BaseModel):
    kind: PredicateKind
    expected: str | bool | int | float | None = Field(
        default=None, validation_alias=AliasChoices("expected", "value"))
    target_ref: str | None = None
    role: str | None = None
    name: str | None = None
    attribute: str | None = None
    custom_name: str | None = None
    case_sensitive: bool = False
    stability: StabilityPolicy = Field(default_factory=StabilityPolicy)

    @property
    def value(self):
        """Compatibility view for the former transaction postcondition."""
        return self.expected


class VerificationOutcome(str, Enum):
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    UNKNOWN = "unknown"


class VerificationResult(BaseModel):
    outcome: VerificationOutcome
    predicate: PredicateKind
    state_id: str
    target: str | None = None
    expected: Any = None
    observed: Any = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    message: str
    samples: int = 1
    consecutive_matches: int = 0
    stable: bool = False
    timed_out: bool = False
    preexisting: bool = False

    def __bool__(self) -> bool:
        return self.passed

    @computed_field
    @property
    def passed(self) -> bool:
        """Compatibility view; callers should branch on ``outcome``."""
        return self.outcome is VerificationOutcome.SATISFIED

    @computed_field
    @property
    def verified(self) -> bool:
        """Compatibility view for the former transaction result."""
        return self.passed

    @computed_field
    @property
    def kind(self) -> PredicateKind:
        return self.predicate


CustomVerifier = Callable[[UIState, UIState | None, VerificationPredicate], tuple[bool, Any, dict[str, Any]]]


class VerificationEngine:
    def __init__(self):
        self._custom: dict[str, CustomVerifier] = {}

    def register(self, name: str, verifier: CustomVerifier) -> None:
        if not name.strip() or name in self._custom:
            raise ValueError("custom verifier name must be non-empty and unique")
        self._custom[name] = verifier

    @staticmethod
    def _elements(state: UIState, predicate: VerificationPredicate,
                  before: UIState | None) -> list[UIElement]:
        target = None
        if predicate.target_ref:
            target = state.element(predicate.target_ref)
            if target is None and before:
                previous = before.element(predicate.target_ref)
                if previous:
                    matches = [element for element in state.elements
                               if any(previous.identity.model_dump().values())
                               and element.identity == previous.identity]
                    if len(matches) == 1:
                        target = matches[0]
                    elif not matches:
                        matches = [element for element in state.elements
                                   if (element.role, element.name) == (previous.role, previous.name)]
                        if len(matches) == 1:
                            target = matches[0]
        if target:
            return [target]
        if predicate.target_ref:
            return []
        return [element for element in state.elements
                if (predicate.role is None or element.role == predicate.role)
                and (predicate.name is None or element.name == predicate.name)]

    @staticmethod
    def _equal(left: Any, right: Any, case_sensitive: bool) -> bool:
        if isinstance(left, str) and isinstance(right, str) and not case_sensitive:
            return left.casefold() == right.casefold()
        return left == right

    def verify(self, state: UIState, predicate: VerificationPredicate,
               *, before: UIState | None = None) -> VerificationResult:
        elements = self._elements(state, predicate, before)
        target = elements[0].ref if len(elements) == 1 else predicate.target_ref
        observed: Any = None
        evidence: dict[str, Any] = {
            "root_id": state.root_id,
            "epoch": state.epoch,
            "observation_degraded": state.quality.degraded,
            "degradation_reasons": list(state.quality.reasons),
        }
        unknown_reason = ""

        if predicate.kind in {"element_exists", "element_missing"}:
            observed = len(elements)
            passed = bool(elements) if predicate.kind == "element_exists" else not elements
            evidence["matches"] = [element.ref for element in elements]
            if predicate.kind == "element_missing" and not elements and state.quality.degraded:
                unknown_reason = "The observation is degraded, so element absence cannot be established."
        elif predicate.kind == "text_equals":
            observed = state.text
            passed = self._equal(observed, predicate.expected, predicate.case_sensitive)
            if not passed and state.quality.degraded:
                unknown_reason = "The observation is degraded, so missing or unequal text is inconclusive."
        elif predicate.kind == "text_contains":
            needle = str(predicate.expected or "")
            haystack = state.text if predicate.case_sensitive else state.text.casefold()
            observed = needle in state.text if predicate.case_sensitive else needle.casefold() in haystack
            passed = bool(observed)
            evidence["text_excerpt"] = self._excerpt(state.text, needle)
            if not passed and state.quality.degraded:
                unknown_reason = "The observation is degraded, so missing text is inconclusive."
        elif predicate.kind == "text_absent":
            needle = str(predicate.expected or "")
            haystack = state.text if predicate.case_sensitive else state.text.casefold()
            observed = needle not in state.text if predicate.case_sensitive else needle.casefold() not in haystack
            passed = bool(observed)
            evidence["text_excerpt"] = self._excerpt(state.text, needle)
            if passed and state.quality.degraded:
                unknown_reason = "The observation is degraded, so text absence cannot be established."
        elif predicate.kind == "value_equals":
            observed = elements[0].value if len(elements) == 1 else None
            passed = len(elements) == 1 and self._equal(observed, predicate.expected, predicate.case_sensitive)
            evidence["matches"] = len(elements)
            if len(elements) != 1:
                unknown_reason = "The value target is missing or ambiguous."
        elif predicate.kind == "checked_equals":
            observed = elements[0].checked if len(elements) == 1 else None
            passed = len(elements) == 1 and observed is predicate.expected
            evidence["matches"] = len(elements)
            if len(elements) != 1:
                unknown_reason = "The checked-state target is missing or ambiguous."
        elif predicate.kind == "url_matches":
            observed = state.url
            try:
                passed = bool(re.search(str(predicate.expected), state.url))
            except re.error:
                passed = False
                evidence["error"] = "invalid regular expression"
                unknown_reason = "The URL predicate is invalid."
        elif predicate.kind == "url_equals":
            observed = state.url
            passed = self._equal(observed, predicate.expected, predicate.case_sensitive)
        elif predicate.kind in {"window_exists", "window_closed"}:
            observed = state.title
            exists = bool(state.title) and (predicate.expected is None
                or str(predicate.expected).casefold() in state.title.casefold())
            passed = exists if predicate.kind == "window_exists" else not exists
            if predicate.kind == "window_closed" and passed and state.quality.degraded:
                unknown_reason = "The observation is degraded, so window absence cannot be established."
        elif predicate.kind == "attribute_equals":
            if len(elements) != 1 or not predicate.attribute:
                observed, passed = None, False
                unknown_reason = "The attribute target is missing or ambiguous."
            else:
                element = elements[0]
                observed = getattr(element, predicate.attribute, element.metadata.get(predicate.attribute))
                passed = self._equal(observed, predicate.expected, predicate.case_sensitive)
            evidence["attribute"] = predicate.attribute
        elif predicate.kind == "state_changed":
            observed = None if before is None else before.content_hash != state.content_hash
            passed = bool(observed)
            evidence["before_state_id"] = before.state_id if before else None
            evidence["before_hash"] = before.content_hash if before else None
            evidence["after_hash"] = state.content_hash
            if before is None:
                unknown_reason = "No baseline observation was supplied."
        else:
            verifier = self._custom.get(predicate.custom_name or "")
            if verifier is None:
                observed, passed = None, False
                evidence["error"] = "custom verifier is not registered"
                unknown_reason = "The custom verifier is not registered."
            else:
                passed, observed, custom_evidence = verifier(state, before, predicate)
                evidence.update(custom_evidence)

        outcome = (VerificationOutcome.UNKNOWN if unknown_reason else
                   VerificationOutcome.SATISFIED if passed else VerificationOutcome.UNSATISFIED)
        return VerificationResult(
            outcome=outcome, predicate=predicate.kind, state_id=state.state_id,
            target=target, expected=predicate.expected, observed=observed, evidence=evidence,
            message=(unknown_reason or ("Verification satisfied." if passed else "Verification unsatisfied.")),
            consecutive_matches=1 if outcome is VerificationOutcome.SATISFIED else 0,
            stable=predicate.stability.consecutive_samples == 1 and outcome is VerificationOutcome.SATISFIED,
        )

    async def wait(self, observe: Callable[[], Awaitable[UIState]], predicate: VerificationPredicate,
                   *, before: UIState | None = None, initial: UIState | None = None,
                   preexisting: bool = False,
                   sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                   monotonic: Callable[[], float] = time.monotonic) -> VerificationResult:
        """Observe until one tri-state result is stable or the bounded deadline expires."""
        policy = predicate.stability
        deadline = monotonic() + min(policy.timeout_ms, 10_000) / 1000
        state = initial
        samples = consecutive = 0
        last: VerificationResult | None = None
        while True:
            if state is None:
                state = await observe()
            samples += 1
            last = self.verify(state, predicate, before=before)
            consecutive = consecutive + 1 if last.outcome is VerificationOutcome.SATISFIED else 0
            if consecutive >= policy.consecutive_samples:
                return last.model_copy(update={
                    "samples": samples, "consecutive_matches": consecutive,
                    "stable": True, "preexisting": preexisting,
                    "message": ("Completion was already satisfied and stable." if preexisting
                                else "Verification satisfied and stable."),
                    "evidence": {**last.evidence, "stability": policy.model_dump()},
                })
            if monotonic() >= deadline:
                if last.outcome is VerificationOutcome.SATISFIED:
                    last = last.model_copy(update={
                        "outcome": VerificationOutcome.UNKNOWN,
                        "message": "The final sample matched, but stability was not established.",
                    })
                return last.model_copy(update={
                    "samples": samples, "consecutive_matches": consecutive,
                    "stable": False, "timed_out": True, "preexisting": preexisting,
                    "evidence": {**last.evidence, "stability": policy.model_dump()},
                })
            await sleep(min(policy.cadence_ms / 1000, max(0.0, deadline - monotonic())))
            state = None

    @staticmethod
    def _excerpt(text: str, needle: str, radius: int = 80) -> str:
        if not needle:
            return text[: radius * 2]
        index = text.casefold().find(needle.casefold())
        if index < 0:
            return text[: radius * 2]
        return text[max(0, index - radius): index + len(needle) + radius]


DEFAULT_VERIFIER = VerificationEngine()


def readiness_predicate(operation: str, *, target_ref: str | None = None,
                        role: str | None = None, name: str | None = None,
                        value: str | None = None, checked: bool | None = None,
                        expected_text: str | None = None,
                        expected_url: str | None = None) -> VerificationPredicate | None:
    """Select a semantic readiness predicate from an operation and explicit expectation."""
    operation = operation.casefold()
    if expected_url:
        return VerificationPredicate(kind="url_equals", expected=expected_url)
    if expected_text:
        return VerificationPredicate(kind="text_contains", expected=expected_text)
    target = {"target_ref": target_ref, "role": role, "name": name}
    if operation in {"type", "fill", "select"} and value is not None:
        return VerificationPredicate(kind="value_equals", expected=value, **target)
    if operation == "set_checked" and checked is not None:
        return VerificationPredicate(kind="checked_equals", expected=checked, **target)
    return None

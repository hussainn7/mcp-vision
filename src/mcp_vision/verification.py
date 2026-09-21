"""Reusable semantic verification over immutable UI states.

Verification returns the evidence used for a decision.  It never promotes a
primitive action receipt into task completion.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from mcp_vision.state import UIElement, UIState


PredicateKind = Literal[
    "element_exists", "element_missing", "text_equals", "text_contains",
    "value_equals", "url_matches", "window_exists", "window_closed",
    "attribute_equals", "state_changed", "custom",
]


class VerificationPredicate(BaseModel):
    kind: PredicateKind
    expected: str | bool | int | float | None = None
    target_ref: str | None = None
    role: str | None = None
    name: str | None = None
    attribute: str | None = None
    custom_name: str | None = None
    case_sensitive: bool = False


class VerificationResult(BaseModel):
    passed: bool
    predicate: PredicateKind
    state_id: str
    target: str | None = None
    expected: Any = None
    observed: Any = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    message: str


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
        evidence: dict[str, Any] = {"root_id": state.root_id, "epoch": state.epoch}

        if predicate.kind in {"element_exists", "element_missing"}:
            observed = len(elements)
            passed = bool(elements) if predicate.kind == "element_exists" else not elements
            evidence["matches"] = [element.ref for element in elements]
        elif predicate.kind == "text_equals":
            observed = state.text
            passed = self._equal(observed, predicate.expected, predicate.case_sensitive)
        elif predicate.kind == "text_contains":
            needle = str(predicate.expected or "")
            haystack = state.text if predicate.case_sensitive else state.text.casefold()
            observed = needle in state.text if predicate.case_sensitive else needle.casefold() in haystack
            passed = bool(observed)
            evidence["text_excerpt"] = self._excerpt(state.text, needle)
        elif predicate.kind == "value_equals":
            observed = elements[0].value if len(elements) == 1 else None
            passed = len(elements) == 1 and self._equal(observed, predicate.expected, predicate.case_sensitive)
            evidence["matches"] = len(elements)
        elif predicate.kind == "url_matches":
            observed = state.url
            try:
                passed = bool(re.search(str(predicate.expected), state.url))
            except re.error:
                passed = False
                evidence["error"] = "invalid regular expression"
        elif predicate.kind in {"window_exists", "window_closed"}:
            observed = state.title
            exists = bool(state.title) and (predicate.expected is None
                or str(predicate.expected).casefold() in state.title.casefold())
            passed = exists if predicate.kind == "window_exists" else not exists
        elif predicate.kind == "attribute_equals":
            if len(elements) != 1 or not predicate.attribute:
                observed, passed = None, False
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
        else:
            verifier = self._custom.get(predicate.custom_name or "")
            if verifier is None:
                observed, passed = None, False
                evidence["error"] = "custom verifier is not registered"
            else:
                passed, observed, custom_evidence = verifier(state, before, predicate)
                evidence.update(custom_evidence)

        return VerificationResult(
            passed=bool(passed), predicate=predicate.kind, state_id=state.state_id,
            target=target, expected=predicate.expected, observed=observed, evidence=evidence,
            message="Verification passed." if passed else "Verification did not pass.",
        )

    @staticmethod
    def _excerpt(text: str, needle: str, radius: int = 80) -> str:
        if not needle:
            return text[: radius * 2]
        index = text.casefold().find(needle.casefold())
        if index < 0:
            return text[: radius * 2]
        return text[max(0, index - radius): index + len(needle) + radius]


DEFAULT_VERIFIER = VerificationEngine()

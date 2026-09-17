"""Three levels of verification, kept distinct.

ACT -> OBSERVE -> COMPARE AGAINST EXPECTATION.

1. Action verification: did the intended interaction happen?
2. State verification:   did the environment enter the expected state?
3. Goal verification:    did this advance / satisfy the user's objective?

An action can succeed while the goal is still incomplete (search loaded, still
comparing). A failed interaction can still reveal useful new information.
"""
from __future__ import annotations

from typing import Any

from mcp_vision.reasoning.schemas import (
    ExpectedOutcome,
    ObservationResult,
    Verification,
)

_OUTCOME = ("success", "partial", "unexpected", "failure", "new_info")


def verify_action(receipt: ObservationResult | None) -> bool:
    """Did the interaction actually happen (not merely the call return)."""
    if receipt is None:
        return False
    return bool(receipt.executed is not False and receipt.ok)


def verify_state(expected: ExpectedOutcome | None, observed: dict[str, Any]) -> tuple[str, str]:
    """Compare the observed environment against the expectation.

    Returns (outcome, detail). Only the light path needs this; the model can
    clamp onto stronger domain checks when available.
    """
    if expected is None:
        return ("new_info", "no explicit expectation; recording observation")
    target = expected.success_signal or ""
    if target and target in _stringify(observed):
        return ("success", target)
    failure = expected.failure_signal
    if failure and failure in _stringify(observed):
        return ("failure", failure)
    if expected.expected_effect and expected.expected_effect in _stringify(observed):
        return ("success", expected.expected_effect)
    return ("unexpected", "no expected signal matched; state changed or still forming")


def _stringify(what: Any) -> str:
    try:
        if isinstance(what, str):
            return what
        if isinstance(what, dict):
            return " ".join(str(v) for v in what.values())
        if isinstance(what, (list, tuple)):
            return " ".join(str(x) for x in what)
        return str(what)
    except Exception:
        return ""


def check_goal(verification: Verification, *, success_conditions: list[str] | None = None) -> Verification:
    """Stamp goal-level success onto a verification, if conditions allow.

    The harness decides whether the objective is *sufficiently* satisfied via the
    completion evaluator; this helper keeps the three tiers explicit.
    """
    if success_conditions is None:
        verification.goal = False
        return verification
    verification.goal = all(condition in verification.detail for condition in success_conditions)
    return verification
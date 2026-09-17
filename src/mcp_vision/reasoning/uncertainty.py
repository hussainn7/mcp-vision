"""Uncertainty resolution policy.

Unknowns should NOT automatically become clarification questions. For each
important unknown, try cheaper, higher-precision sources first, in order:

    context -> observe environment -> discover via tools -> infer confidently
        -> reversible assumption -> does it matter? -> is a consequential
        action dependent on it? -> only then ask the user.

Ask late. Keep the user's time for genuine, consequential gaps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mcp_vision.reasoning.schemas import (
    AgentState,
    Assumption,
    ConsequenceLevel,
    Uncertainty,
)

RESOLUTION_ORDER = (
    "context",
    "observe",
    "discover",
    "infer",
    "assume",
)


@dataclass
class Resolver:
    """Sources the harness can consult, cheapest / most grounded first."""

    context_getter: Any = None  # () -> Any  (from existing request/conversation)
    observer: Any = None  # () -> Any  (from the current environment)
    discoverer: Any = None  # () -> Any  (search / tools)
    inferrer: Any = None  # (question) -> (confidence, value) | None
    # Hook for callers to decide whether a question is safe to assume. Default:
    # safe when it is reversible and nothing consequential depends on it.
    assumable: Any = None  # (uncertainty, gate) -> Assumption | None


@dataclass
class Resolution:
    how: str = ""  # context|observe|discover|infer|assume|ask|not_needed
    value: Any = None
    assumption: Assumption | None = None
    question: str = ""
    note: str = ""


def resolve_unknown(state: AgentState, uncertainty: Uncertainty, *, resolver: Resolver,
                    gate: ConsequenceLevel = ConsequenceLevel.NONE) -> Resolution:
    """Resolve one unknown, preferring assumption over asking, ask last."""
    if not uncertainty.materially_affects:
        return Resolution(how="not_needed", note="does not materially affect the result")

    for how in RESOLUTION_ORDER:
        try:
            if how == "context" and resolver.context_getter is not None:
                value = resolver.context_getter()
                if _truthy(value):
                    return Resolution(how="context", value=value, note="resolved from existing context")
            elif how == "observe" and resolver.observer is not None:
                value = resolver.observer()
                if _truthy(value):
                    return Resolution(how="observe", value=value, note="observed from the environment")
            elif how == "discover" and resolver.discoverer is not None:
                value = resolver.discoverer()
                if _truthy(value):
                    return Resolution(how="discover", value=value, note="discovered via tools")
            elif how == "infer" and resolver.inferrer is not None:
                result = resolver.inferrer(uncertainty.question)
                if result is not None and _truthy(result):
                    confidence, value = result
                    if float(confidence) >= 0.6:
                        return Resolution(how="infer", value=value, note="confident inference")
        except Exception:
            continue

    # No grounded source gave anything. Decide between assuming and asking.
    assumed: Assumption | None = None
    if resolver.assumable is not None:
        try:
            assumed = resolver.assumable(uncertainty, gate)
        except Exception:
            assumed = None
    if assumed is None:
        # Default: an assumption is fine while research stays low-consequence and
        # reversible. Once a consequential action depends on it, ask instead.
        if int(gate) >= int(uncertainty.blocks_consequence):
            return Resolution(
                how="ask",
                question=uncertainty.question,
                note="consequential action depends on this; do not guess",
            )
        assumed = Assumption(
            statement=uncertainty.question,
            basis="temporary reversible assumption for low-risk progress",
            reversible=True,
            verify_before=uncertainty.blocks_consequence,
        )
    return Resolution(how="assume", assumption=assumed,
                      note=f"temporary assumption while researching: {assumed.statement}")


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    text = str(value).strip()
    return bool(text) and text.lower() not in {"", "none", "unknown", "n/a", "?"}


def context_resolution_order() -> list[str]:
    """Order in which to try resolving context before asking the user."""
    return [
        "current request",
        "current conversation",
        "working state",
        "current screen",
        "selected text",
        "focused element",
        "current application",
        "open tabs",
        "available files",
        "connected data",
        "verified user preferences",
        "external research",
        "reasonable reversible assumption",
        "user clarification",
    ]
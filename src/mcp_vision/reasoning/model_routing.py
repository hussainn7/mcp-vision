"""Model routing: route work to a fast / general / strong reasoning tier.

Simple or obvious -> fast path. Ambiguous -> general reasoner. Stalled, complex,
consequence-heavy -> stronger review. We don't ask the most expensive model for
trivial decisions, and we don't hold a committee conversation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from mcp_vision.reasoning.schemas import ConsequenceLevel

TIER_FAST = "fast"
TIER_GENERAL = "general"
TIER_STRONG = "strong"

# Trigger signals that warrant the stronger tier.
_CONSEQUENCE_WORTHY = (ConsequenceLevel.SIGNIFICANT, ConsequenceLevel.CRITICAL)


@dataclass
class Meta:
    """Cheap signals for routing + meta-reasoning triggers."""

    ambiguity: bool = False
    stalls: int = 0
    hard_failures: int = 0
    consequence: ConsequenceLevel = ConsequenceLevel.NONE
    meta_due: bool = False
    notes: list[str] = field(default_factory=list)


def route_tier(meta: Meta) -> str:
    """Pick a reasoning tier for the next decision."""
    if meta.consequence in _CONSEQUENCE_WORTHY or meta.meta_due or meta.hard_failures >= 2:
        return TIER_STRONG
    if meta.ambiguity or meta.stalls >= 2:
        return TIER_GENERAL
    return TIER_FAST


def meta_reason_due(state, *, every: int = 6) -> bool:
    """Run meta-reasoning after stalls, surprises, or on a cadence for long tasks."""
    if state.progress.non_progress_steps >= 2:
        return True
    if state.meta_checks == 0 and len(state.observations) >= every:
        return True
    return state.meta_checks <= (len(state.observations) // every)
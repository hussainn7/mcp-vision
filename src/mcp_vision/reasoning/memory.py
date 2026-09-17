"""Memory separation: working vs session vs durable preferences vs capability.

Task assumptions and temporary working facts must never silently become durable
user preferences. Only reliable, repeatedly-reinforced facts are promoted to
durable preference storage. Capability knowledge (reusable strategies and traps)
is a separate bucket for future learned skills.
"""
from __future__ import annotations

from mcp_vision.reasoning.schemas import AgentState, P_VERIFIED

# A durable preference needs this much confidence *and* this much reinforcement.
_MIN_DURABLE_CONFIDENCE = 0.9
_MIN_REINFORCEMENT = 2


def ingest_observation(state: AgentState, observation: dict) -> None:
    """Record a grounded observation into working memory, not durable prefs."""
    state.observations.append(observation)
    state.memory.working["last_observation"] = observation
    state.memory.session["session_seen"] = state.memory.session.get("session_seen", 0) + 1


def reinforce(state: AgentState, note: str, value: object) -> None:
    """Mark a belief as reinforced (fresh/observed again)."""
    for f in state.facts:
        if f.note == note:
            f.freshness = 1.0
            f.confidence = min(1.0, f.confidence + 0.1)
    counts = state.memory.working.setdefault("reinforce_count", {})
    counts[note] = counts.get(note, 0) + 1


def promote_to_durable(state: AgentState, note: str) -> bool:
    """Promote a belief to durable preferences only if it is reliable *and*
    observed enough. Returns True when promoted."""
    belief = next((f for f in state.facts if f.note == note), None)
    if belief is None or belief.source != P_VERIFIED:
        return False
    if float(belief.confidence) < _MIN_DURABLE_CONFIDENCE:
        return False
    reinforces = state.memory.working.get("reinforce_count", {}).get(note, 0)
    if reinforces < _MIN_REINFORCEMENT:
        return False
    state.memory.durable_preferences[note] = belief.value
    return True
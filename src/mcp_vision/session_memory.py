"""Bounded long-run memory derived from structured evidence, not screenshots."""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field


class SessionMemory(BaseModel):
    completed_milestones: list[str] = Field(default_factory=list)
    important_observations: list[str] = Field(default_factory=list)
    resolved_decisions: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    known_failures: list[str] = Field(default_factory=list)
    verified_outcomes: list[str] = Field(default_factory=list)
    recent_events: list[dict[str, Any]] = Field(default_factory=list)
    original_chars: int = 0
    compact_chars: int = 0
    savings_ratio: float = 0


def _append_unique(items: list[str], value: str, limit: int = 12) -> None:
    value = " ".join(value.split())[:180]
    if value and value not in items:
        items.append(value)
        del items[:-limit]


def compact_replay(bundle: dict[str, Any], *, keep_recent: int = 12) -> SessionMemory:
    """Keep recent evidence rich while folding older events into durable categories."""
    if not 1 <= keep_recent <= 50:
        raise ValueError("keep_recent must be between 1 and 50")
    events = list(bundle.get("events") or [])
    memory = SessionMemory(recent_events=events[-keep_recent:])
    for event in events[:-keep_recent]:
        kind = event.get("type")
        if kind == "fastpath_end":
            status = event.get("status", "unknown")
            subgoal = event.get("subgoal", "subgoal")
            destination = memory.completed_milestones if status == "verified" else memory.known_failures
            _append_unique(destination, f"{subgoal}: {status} — {event.get('reason', '')}")
        elif kind == "observation":
            _append_unique(memory.important_observations,
                           f"Observed {event.get('source', 'UI')} state {event.get('state_id', '?')} "
                           f"with {event.get('elements', '?')} elements at {event.get('url') or event.get('title') or 'current root'}")
        elif kind == "policy_decision":
            _append_unique(memory.resolved_decisions,
                           f"{event.get('policy', 'policy')} chose {event.get('candidate_id') or 'replan'} "
                           f"at {float(event.get('confidence') or 0):.0%}: {event.get('reason', '')}")
        elif kind in {"verification", "postcondition"}:
            text = f"{event.get('predicate') or event.get('kind')}: expected {event.get('expected')}"
            _append_unique(memory.verified_outcomes if event.get("passed", event.get("verified"))
                           else memory.known_failures, text)
        elif event.get("status") in {"blocked", "stale", "error"}:
            _append_unique(memory.known_failures,
                           f"{kind}: {event.get('message') or event.get('reason') or event.get('status')}")
        if event.get("needs_system2"):
            _append_unique(memory.constraints, f"System-2 required: {event.get('reason', 'uncertain decision')}")
        if event.get("risk") == "RESTRICTED_ACTION":
            _append_unique(memory.constraints, f"Restricted candidate: {event.get('operation', 'action')}")
    memory.original_chars = len(json.dumps(bundle, default=str, separators=(",", ":")))
    compact = memory.model_dump(exclude={"original_chars", "compact_chars", "savings_ratio"})
    memory.compact_chars = len(json.dumps(compact, default=str, separators=(",", ":")))
    memory.savings_ratio = (round(1 - memory.compact_chars / memory.original_chars, 4)
                            if memory.original_chars else 0)
    return memory

"""Privacy-safe milestone timing for contextual interactions."""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp_vision.paths import state_dir


class InteractionTimeline:
    """Append milestone timings without retaining audio or transcript content."""

    _write_lock = threading.Lock()

    def __init__(self, kind: str, *, path: Path | None = None):
        self.interaction_id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.started = time.perf_counter()
        self.path = path or state_dir() / "interaction_metrics.jsonl"
        self.seen: set[str] = set()
        self.mark("interaction_started")

    def mark(self, milestone: str, **fields: Any) -> None:
        if milestone in self.seen:
            return
        self.seen.add(milestone)
        event = {
            "ts": round(time.time(), 4),
            "interaction_id": self.interaction_id,
            "kind": self.kind,
            "milestone": milestone,
            "elapsed_ms": round((time.perf_counter() - self.started) * 1000, 1),
            **fields,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._write_lock, self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=True, default=str) + "\n")
        except OSError:
            pass


MILESTONES = (
    "input_started", "first_partial", "stable_partial", "preparation_started",
    "app_observed", "window_captured", "browser_attached", "provider_warm",
    "app_resolved", "final_transcript", "preparation_adopted",
    "preparation_discarded", "clarification_requested", "clarification_answered",
    "first_useful_action", "verification_complete", "cancelled",
)


@dataclass
class InteractionMetrics:
    task_id: str
    clock: Any = time.monotonic
    events: list[dict] = field(default_factory=list)

    def mark(self, name: str, *, generation: int | None = None, **details) -> float:
        if name not in MILESTONES:
            raise ValueError(f"unknown interaction milestone: {name}")
        at = self.clock()
        if self.events and at < self.events[-1]["at"]:
            raise ValueError("interaction milestones must be monotonic")
        self.events.append({"name": name, "at": at, "generation": generation, **details})
        return at

    def first(self, name: str) -> float | None:
        return next((event["at"] for event in self.events if event["name"] == name), None)

    def latency(self, start: str, end: str) -> float | None:
        a, b = self.first(start), self.first(end)
        return None if a is None or b is None else max(0.0, b - a)

    def summary(self) -> dict[str, float]:
        pairs = {
            "speech_end_to_action": ("final_transcript", "first_useful_action"),
            "input_to_action": ("input_started", "first_useful_action"),
            "preparation_lead": ("preparation_started", "final_transcript"),
            "action_to_verified": ("first_useful_action", "verification_complete"),
        }
        return {key: value for key, pair in pairs if (value := self.latency(*pair)) is not None}

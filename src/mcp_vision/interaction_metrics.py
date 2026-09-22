"""Privacy-safe milestone timing for contextual interactions."""
from __future__ import annotations

import json
import threading
import time
import uuid
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

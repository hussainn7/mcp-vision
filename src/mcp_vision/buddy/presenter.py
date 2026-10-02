"""Turn engine and push-to-talk events into Island / Blip UI messages.

Platform-neutral: ``post_island`` receives lists of bridge messages (see
``ui/src/bridge.ts``) and ``set_mood`` receives Blip's mood and level. The
macOS host hops both onto the main thread and into the web views.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

Message = dict[str, Any]


class Presenter:
    LEVEL_INTERVAL = 0.05          # at most 20 level updates per second

    def __init__(self, post_island: Callable[[list[Message]], None],
                 set_mood: Callable[[str, float], None] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.post_island = post_island
        self.set_mood = set_mood or (lambda mood, level: None)
        self.clock = clock
        self._last_level = 0.0
        self.phase = "idle"
        self.walkthrough: dict[str, Any] | None = None

    def _island(self, **state: Any) -> None:
        self.post_island([{"type": "island", "state": state}])

    # -- push-to-talk ------------------------------------------------------------
    def listening(self) -> None:
        self.phase = "listening"
        self.walkthrough = None
        self.post_island([{"type": "reset"}, {"type": "island", "state": {"phase": "listening"}}])
        self.set_mood("listening", 0.0)

    def level(self, value: float) -> None:
        now = self.clock()
        if now - self._last_level < self.LEVEL_INTERVAL:
            return
        self._last_level = now
        value = round(max(0.0, min(1.0, value)), 3)
        self._island(level=value)
        if self.phase == "listening":
            self.set_mood("listening", value)

    def transcript(self, text: str) -> None:
        self._island(transcript=text)

    def thinking(self) -> None:
        self.phase = "thinking"
        self._island(phase="thinking", level=0)
        self.set_mood("thinking", 0.0)

    def idle(self) -> None:
        self.phase = "idle"
        self._island(phase="idle", level=0)
        self.set_mood("idle", 0.0)

    def failed(self, message: str) -> None:
        self.phase = "error"
        self._island(phase="error", error=message)
        self.set_mood("error", 0.0)

    # -- companion observer ------------------------------------------------------------
    def __call__(self, kind: str, data: dict[str, Any]) -> None:
        handler = getattr(self, f"_on_{kind}", None)
        if handler is not None:
            handler(data)

    def _on_phase(self, data: dict[str, Any]) -> None:
        phase = data.get("phase")
        if phase == "thinking":
            self.phase = "thinking"
            state: dict[str, Any] = {"phase": "thinking", "done": False, "level": 0}
            if data.get("guide"):
                # A walkthrough check-in: fresh step text, keep progress.
                state.update(answer="", steps=[], walkthrough=self.walkthrough)
            elif data.get("transcript"):
                state["transcript"] = data["transcript"]
            self._island(**state)
            self.set_mood("thinking", 0.0)
        elif phase == "answering":
            self.phase = "answering"
            self._island(phase="answering", done=False)
            self.set_mood("speaking", 0.5)

    def _on_step(self, data: dict[str, Any]) -> None:
        step = {key: data[key] for key in ("id", "label", "status", "detail") if data.get(key)}
        self.post_island([{"type": "step", "step": step}])

    def _on_engine(self, data: dict[str, Any]) -> None:
        self._island(engine={key: data.get(key) for key in ("label", "kind", "model")})

    def _on_answer(self, data: dict[str, Any]) -> None:
        self.post_island([{"type": "append", "field": "answer", "text": data.get("text", "")}])

    def _on_walkthrough(self, data: dict[str, Any]) -> None:
        self.walkthrough = {"index": int(data.get("index", 0)), "total": int(data.get("total", 1)),
                            "label": data.get("label") or ""}
        messages: list[Message] = [{"type": "island", "state": {"walkthrough": self.walkthrough}}]
        if data.get("waiting"):
            messages.append({"type": "step", "step": {"id": "wait", "label": "Your turn. I'm watching", "status": "active"}})
        elif data.get("timed_out"):
            messages.append({"type": "step", "step": {"id": "wait", "label": "Paused. Ask me to continue", "status": "skipped"}})
        elif data.get("finished"):
            messages.append({"type": "step", "step": {"id": "wait", "label": "All done", "status": "done"}})
            self.set_mood("happy", 0.0)
        self.post_island(messages)

    def _on_done(self, data: dict[str, Any]) -> None:
        latency = data.get("latency_ms")
        self._island(done=True, latencyMs=round(latency) if isinstance(latency, (int, float)) else None)
        self.set_mood("happy" if not self.walkthrough else "idle", 0.0)

    def _on_error(self, data: dict[str, Any]) -> None:
        self.failed(data.get("message") or "Something went wrong.")

    def _on_point(self, data: dict[str, Any]) -> None:
        # Flight and the label bubble are driven by the native animator.
        pass

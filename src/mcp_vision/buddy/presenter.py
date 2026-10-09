"""Turn engine and push-to-talk events into Island / Plip UI messages.

Platform-neutral: ``post_island`` receives lists of bridge messages (see
``ui/src/bridge.ts``) and ``set_mood`` receives Plip's mood and level. The
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
        self.plan: list[str] = []
        self.goal: dict[str, Any] | None = None       # the multi-step task's island step, kept across check-ins

    def _island(self, **state: Any) -> None:
        self.post_island([{"type": "island", "state": state}])

    # -- push-to-talk ------------------------------------------------------------
    def listening(self) -> None:
        self.phase = "listening"
        self.walkthrough = None
        self.plan = []
        self.goal = None
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
        self._island(phase="idle", level=0, speaking=False)
        self.set_mood("idle", 0.0)

    def failed(self, message: str, setup: bool | None = None) -> None:
        """``setup``: the fix is in settings (no brain, no permission); by default when the message says so."""
        self.phase = "error"
        fixable = "settings" in message.lower() if setup is None else setup
        self._island(phase="error", error=message, fixable=fixable)
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
                # A walkthrough check-in or a task's next step: fresh step text, keep progress.
                state.update(answer="", steps=[], walkthrough=self.walkthrough)
                if self.goal is not None:
                    self._island(**state)
                    self.post_island([{"type": "step", "step": dict(self.goal)}])
                    self.set_mood("thinking", 0.0)
                    return
            elif data.get("transcript"):
                state["transcript"] = data["transcript"]
            self._island(**state)
            self.set_mood("thinking", 0.0)
        elif phase == "answering":
            self.phase = "answering"
            self._island(phase="answering", done=False, speaking=True)
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
        state: dict[str, Any] = {"walkthrough": self.walkthrough}
        if self.plan:          # the step being worked on; all checked once finished
            state["planIndex"] = len(self.plan) if data.get("finished") else self.walkthrough["index"]
        messages: list[Message] = [{"type": "island", "state": state}]
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
        working = self.walkthrough or (self.goal is not None and self.goal["status"] == "active")
        self.set_mood("happy" if not working else "idle", 0.0)

    def _on_quiet(self, _data: dict[str, Any]) -> None:
        """Plip stopped talking: only now may a finished answer tuck back into the notch."""
        self._island(speaking=False)

    def _on_goal(self, data: dict[str, Any]) -> None:
        """A multi-step task: one chip that says what it's working toward and which step it's on."""
        text = str(data.get("text") or "")[:80]
        if not text:
            return
        status = "done" if data.get("done") else "skipped" if data.get("paused") else "active"
        detail = "say keep going" if data.get("paused") else f"step {data['step']}" if data.get("step") else ""
        self.goal = {"id": "goal", "label": f"Goal: {text}", "status": status, **({"detail": detail} if detail else {})}
        self.post_island([{"type": "step", "step": dict(self.goal)}])

    def _on_error(self, data: dict[str, Any]) -> None:
        self.failed(data.get("message") or "Something went wrong.")

    def _on_plan(self, data: dict[str, Any]) -> None:
        self.plan = [str(step) for step in data.get("steps") or []][:10]
        self._island(plan=self.plan, planIndex=0)

    def _on_confirm(self, data: dict[str, Any]) -> None:
        if data.get("cleared"):
            self._island(confirm=None)
            return
        self._island(confirm={"title": data.get("title", ""), "lines": list(data.get("lines") or [])[:6],
                              "confirm": data.get("confirm") or "Do it", "name": data.get("name", "")})
        self.set_mood("thinking", 0.0)

    def _on_action(self, data: dict[str, Any]) -> None:
        items = data.get("items") or []
        if items:
            self._island(results=[{key: item.get(key) for key in ("title", "detail", "path")} for item in items[:6]])

    def _on_notice(self, data: dict[str, Any]) -> None:
        self.post_island([{"type": "reset"}, {"type": "island", "state": {
            "phase": "answering", "answer": data.get("text", ""), "done": True}}])
        self.set_mood("happy", 0.0)

    def _on_point(self, data: dict[str, Any]) -> None:
        # Flight and the label bubble are driven by the native animator.
        pass

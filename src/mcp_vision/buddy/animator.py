"""The buddy cursor's behavior, independent of any drawing toolkit.

``tick(now, mouse)`` advances the state machine and returns what to draw:

    follow ──point()──▶ fly_out ──arrive──▶ pointing ──hold──▶ fly_back ──▶ follow
                          ▲                    │ next target queued
                          └────────────────────┘

Timings follow Clicky: the buddy trails the cursor at (+35, +25), lands at
(+8, +12) from the target kept 20 pt inside the screen, types its label into
a bubble, holds ~3 s, fades the bubble, and flies home. Moving the mouse more
than 100 pt during the return snaps it back to following.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from mcp_vision.buddy.flight import REST_ANGLE, FlightPlan
from mcp_vision.buddy.geometry import Rect

FOLLOW_OFFSET = (35.0, 25.0)
TARGET_OFFSET = (8.0, 12.0)
EDGE_INSET = 20.0
TYPE_INTERVAL = 0.045       # seconds per bubble character
HOLD = 3.0                  # after the label is typed
CHAIN_HOLD = 1.1            # shorter hold when another target is waiting
MAX_SPEAKING_HOLD = 8.0     # keep pointing while the sentence is still being spoken
BUBBLE_FADE = 0.5
RETURN_CANCEL_DISTANCE = 100.0
FOLLOW_SMOOTHING = 0.45     # fraction of the remaining gap closed per frame
MAX_LABEL = 40


@dataclass(frozen=True)
class RenderState:
    x: float
    y: float
    angle: float
    scale: float
    mode: str
    voice: str
    level: float
    bubble: str
    bubble_alpha: float
    visible: bool = True


@dataclass(frozen=True)
class _Target:
    x: float
    y: float
    label: str


class BuddyAnimator:
    def __init__(self, screens: Callable[[], list[Rect]] | None = None):
        self.screens = screens or (lambda: [])
        self.mode = "follow"
        self.voice = "idle"
        self.level = 0.0
        self.x = self.y = None
        self.angle = REST_ANGLE
        self.scale = 1.0
        self._queue: list[_Target] = []
        self._plan: FlightPlan | None = None
        self._started = 0.0
        self._label = ""
        self._typed_done = 0.0
        self._fade_from: float | None = None
        self._return_anchor = (0.0, 0.0)
        self._release_requested = False

    # -- commands (any thread may call these via the UI thread) ---------------
    def point(self, x: float, y: float, label: str) -> None:
        tx, ty = self._landing(x, y)
        self._queue.append(_Target(tx, ty, " ".join(label.split())[:MAX_LABEL]))
        self._release_requested = False

    def release(self) -> None:
        self._queue.clear()
        self._release_requested = True

    def set_voice(self, state: str) -> None:
        self.voice = state

    def set_level(self, level: float) -> None:
        # Fast attack, slow decay, like a VU meter.
        self.level = max(min(max(level, 0.0), 1.0), self.level * 0.72)

    @property
    def busy(self) -> bool:
        return self.mode != "follow" or bool(self._queue)

    # -- per-frame update -------------------------------------------------------
    def tick(self, now: float, mouse: tuple[float, float]) -> RenderState:
        home = (mouse[0] + FOLLOW_OFFSET[0], mouse[1] + FOLLOW_OFFSET[1])
        if self.x is None:
            self.x, self.y = home
        if self._release_requested and self.mode in {"fly_out", "pointing"}:
            self._fly_back(now, mouse)
        self._release_requested = False

        if self.mode == "follow":
            gap = math.hypot(home[0] - self.x, home[1] - self.y)
            if gap > 600:                      # jumped to another display: don't streak across
                self.x, self.y = home
            else:
                self.x += (home[0] - self.x) * FOLLOW_SMOOTHING
                self.y += (home[1] - self.y) * FOLLOW_SMOOTHING
            self._settle_pose()
            if self._queue:
                self._fly_to_next(now)

        if self.mode == "fly_out":
            frame = self._plan.frame_at(now - self._started)
            self.x, self.y, self.angle, self.scale = frame.x, frame.y, frame.angle, frame.scale
            if frame.done:
                self.mode = "pointing"
                self._started = now
                self._typed_done = now + len(self._label) * TYPE_INTERVAL
                self._fade_from = None
                self.scale = 1.0

        bubble, alpha = "", 0.0
        if self.mode == "pointing":
            self._settle_pose()
            typed = int((now - self._started) / TYPE_INTERVAL) + 1
            bubble = self._label[:max(0, typed)]
            alpha = 1.0
            held = now - self._typed_done
            if self._queue and held >= CHAIN_HOLD:
                self._fly_to_next(now)
                bubble, alpha = "", 0.0
            else:
                still_talking = self.voice == "speaking" and held < MAX_SPEAKING_HOLD
                if self._fade_from is None and held >= HOLD and not still_talking:
                    self._fade_from = now
                if self._fade_from is not None:
                    alpha = max(0.0, 1.0 - (now - self._fade_from) / BUBBLE_FADE)
                    if alpha <= 0.0:
                        self._fly_back(now, mouse)
                        bubble = ""

        if self.mode == "fly_back":
            if math.hypot(mouse[0] - self._return_anchor[0], mouse[1] - self._return_anchor[1]) > RETURN_CANCEL_DISTANCE:
                self.mode = "follow"
                self.x, self.y = home
                self.scale = 1.0
                self._settle_pose()
            else:
                frame = self._plan.frame_at(now - self._started)
                self.x, self.y, self.angle, self.scale = frame.x, frame.y, frame.angle, frame.scale
                if frame.done:
                    self.mode = "follow"
                    self.scale = 1.0
                    if self._queue:
                        self._fly_to_next(now)

        return RenderState(x=self.x, y=self.y, angle=self.angle, scale=self.scale, mode=self.mode,
                           voice=self.voice, level=self.level, bubble=bubble, bubble_alpha=alpha)

    # -- internals ----------------------------------------------------------------
    def _fly_to_next(self, now: float) -> None:
        target = self._queue.pop(0)
        self._label = target.label
        self._plan = FlightPlan.between((self.x, self.y), (target.x, target.y))
        self._started = now
        self.mode = "fly_out"

    def _fly_back(self, now: float, mouse: tuple[float, float]) -> None:
        home = (mouse[0] + FOLLOW_OFFSET[0], mouse[1] + FOLLOW_OFFSET[1])
        self._plan = FlightPlan.between((self.x, self.y), home)
        self._started = now
        self._return_anchor = mouse
        self.mode = "fly_back"

    def _settle_pose(self) -> None:
        self.angle += (REST_ANGLE - self.angle) * 0.25
        self.scale += (1.0 - self.scale) * 0.3

    def _landing(self, x: float, y: float) -> tuple[float, float]:
        tx, ty = x + TARGET_OFFSET[0], y + TARGET_OFFSET[1]
        screen = next((s for s in self.screens() if s.contains(x, y)), None)
        if screen is None:
            return tx, ty
        return (min(max(tx, screen.x + EDGE_INSET), screen.x + screen.width - EDGE_INSET),
                min(max(ty, screen.y + EDGE_INSET), screen.y + screen.height - EDGE_INSET))

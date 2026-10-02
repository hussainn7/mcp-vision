"""Blip next to your cursor (macOS): native motion, web-rendered character.

The BuddyAnimator decides where Blip is each frame (follow the cursor, arc to
a target, hold, fly home); this window follows it at 60 Hz. The web view
draws Blip and the typed label bubble and only hears about changes: mood,
where Blip looks, and how it leans while flying.
"""
from __future__ import annotations

import math
import time
from typing import Any

from mcp_vision.buddy.animator import BuddyAnimator, RenderState
from mcp_vision.buddy.overlay_macos import _ticker_class, mouse_global, screen_rects
from mcp_vision.buddy.web_host import WebSurface

WIDTH, HEIGHT = 260.0, 96.0
ANCHOR = (26.0, 26.0)               # Blip's center inside the web view (MascotView.MASCOT_ANCHOR)
SEND_INTERVAL = 1 / 30


def mascot_state(render: RenderState, mood: str, level: float, velocity: tuple[float, float],
                 mouse: tuple[float, float], label: str) -> dict[str, Any]:
    """What the web mascot should show for this frame (pure; tested)."""
    flying = render.mode in {"fly_out", "fly_back"}
    pointing = render.mode == "pointing" or render.mode == "fly_out"
    vx, vy = velocity
    speed = math.hypot(vx, vy)
    if flying and speed > 0.5:
        look = (vx / speed, vy / speed)
    elif render.mode == "pointing":
        look = (-0.7, -0.7)                       # the target sits up-left of where Blip lands
    else:
        dx, dy = mouse[0] - render.x, mouse[1] - render.y
        distance = math.hypot(dx, dy) or 1.0
        look = (dx / distance, dy / distance)     # idle Blip keeps an eye on your cursor
    lean = max(-16.0, min(16.0, vx * 0.9)) if flying else 0.0
    return {
        "mood": "pointing" if pointing else mood,
        "level": round(level, 2),
        "lean": round(lean, 1),
        "look": {"x": round(look[0], 2), "y": round(look[1], 2)},
        "label": label if render.mode == "pointing" else "",
    }


class MascotWindow:
    """Implements the companion's Pointer port. Main thread only."""

    def __init__(self, *, visible: bool = True):
        import AppKit

        self.animator = BuddyAnimator(screens=screen_rects)
        self.visible = visible
        self.mood = "idle"
        self.level = 0.0
        self._last_sent: dict[str, Any] | None = None
        self._last_send_time = 0.0
        self._last_xy: tuple[float, float] | None = None
        rect = AppKit.NSMakeRect(0, 0, WIDTH, HEIGHT)
        window = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered, False)
        window.setOpaque_(False)
        window.setBackgroundColor_(AppKit.NSColor.clearColor())
        window.setHasShadow_(False)
        window.setIgnoresMouseEvents_(True)
        window.setReleasedWhenClosed_(False)
        window.setHidesOnDeactivate_(False)
        window.setLevel_(AppKit.NSScreenSaverWindowLevel)
        window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorIgnoresCycle)
        try:
            window.setSharingType_(AppKit.NSWindowSharingNone)     # never in Blip's own screenshots
        except Exception:
            pass
        self.surface = WebSurface("mascot", rect, lambda _command: None)
        window.setContentView_(self.surface.view)
        self.window = window
        if visible:
            window.orderFrontRegardless()
        self._ticker = _ticker_class().alloc().initWithCallback_(self.tick)
        self._timer = AppKit.NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            1 / 60, self._ticker, "fire:", None, True)
        AppKit.NSRunLoop.mainRunLoop().addTimer_forMode_(self._timer, AppKit.NSRunLoopCommonModes)

    # -- Pointer port + presenter hooks -------------------------------------------------
    def set_state(self, state: str, detail: str = "") -> None:
        self.animator.set_voice(state)
        if state != "idle":
            self.window.orderFrontRegardless()

    def set_mood(self, mood: str, level: float = 0.0) -> None:
        self.mood = mood
        self.level = level

    def point(self, x: float, y: float, label: str) -> None:
        self.animator.point(x, y, label)
        self.window.orderFrontRegardless()

    def release(self) -> None:
        self.animator.release()

    def set_level(self, level: float) -> None:
        self.level = level

    def set_visible(self, visible: bool) -> None:
        self.visible = visible
        if visible:
            self.window.orderFrontRegardless()

    # -- frame loop -----------------------------------------------------------------------
    def tick(self) -> None:
        import AppKit

        now = time.monotonic()
        mouse = mouse_global()
        render = self.animator.tick(now, mouse)
        primary_height = float(AppKit.NSScreen.screens()[0].frame().size.height)
        idle = render.mode == "follow" and render.voice == "idle" and self.mood in {"idle", "happy"}
        if not self.visible and idle:
            if self.window.isVisible():
                self.window.orderOut_(None)
            return
        self.window.setFrameOrigin_(AppKit.NSMakePoint(render.x - ANCHOR[0],
                                                       primary_height - (render.y - ANCHOR[1]) - HEIGHT))
        velocity = (0.0, 0.0) if self._last_xy is None else (render.x - self._last_xy[0], render.y - self._last_xy[1])
        self._last_xy = (render.x, render.y)
        state = mascot_state(render, self.mood, self.level, velocity, mouse, self.animator.label)
        if state != self._last_sent and now - self._last_send_time >= SEND_INTERVAL:
            self._last_sent = state
            self._last_send_time = now
            self.surface.post([{"type": "mascot", "state": state}])

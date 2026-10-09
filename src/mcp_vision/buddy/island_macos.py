"""The Island: Plip's Dynamic-Island-style home in the MacBook notch.

A borderless, non-activating panel spans the top-center of the notched
display (or the main display on Macs without a notch, where it draws its own
pill). It floats above the menu bar on every Space and in full-screen apps,
stays out of screenshots, and passes clicks through everywhere except the
island's current shape, which the web UI reports as it animates.
"""
from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from typing import Any

from mcp_vision.buddy.web_host import WebSurface

WIDTH, HEIGHT = 760.0, 420.0
YIELD_SECONDS = 1.0                            # Plip's click/scroll/drag owns the screen this long
PARK_SLOP = 3.0                                # points a parked cursor drifts before it's the user's
HOVER_MARGIN = 6.0
_CLASSES: dict[str, type] = {}


def notch_screen():
    """The built-in notched display if there is one, else the main display."""
    import AppKit

    for screen in AppKit.NSScreen.screens():
        if _notch_height(screen) > 0:
            return screen
    return AppKit.NSScreen.mainScreen() or AppKit.NSScreen.screens()[0]


def _notch_height(screen: Any) -> float:
    try:
        return float(screen.safeAreaInsets().top)
    except Exception:
        return 0.0


def notch_geometry(screen: Any) -> dict[str, Any]:
    """Physical notch size in points, or a menu-bar-height virtual notch."""
    height = _notch_height(screen)
    frame = screen.frame()
    if height > 0:
        try:
            left = screen.auxiliaryTopLeftArea()
            right = screen.auxiliaryTopRightArea()
            width = float(frame.size.width) - float(left.size.width) - float(right.size.width)
            if 80 < width < 400:
                return {"width": round(width), "height": round(height), "hasNotch": True}
        except Exception:
            pass
        return {"width": 200, "height": round(height), "hasNotch": True}
    menu_bar = float(frame.origin.y + frame.size.height) - float(screen.visibleFrame().origin.y + screen.visibleFrame().size.height)
    return {"width": 190, "height": round(menu_bar if 18 <= menu_bar <= 40 else 26), "hasNotch": False}


def island_hit(mouse: tuple[float, float], window_frame: tuple[float, float, float, float],
               island: tuple[float, float]) -> bool:
    """Is the mouse (AppKit points) over the visible island (centered at the window's top)?"""
    x, y, width, height = window_frame
    island_w, island_h = island
    left = x + (width - island_w) / 2 - HOVER_MARGIN
    right = left + island_w + 2 * HOVER_MARGIN
    top = y + height
    bottom = top - island_h - HOVER_MARGIN
    return left <= mouse[0] <= right and bottom <= mouse[1] <= top


def _ticker_class():
    if "ticker" not in _CLASSES:
        import Foundation
        import objc

        class IslandTicker(Foundation.NSObject):
            def initWithCallback_(self, callback):
                self = objc.super(IslandTicker, self).init()
                if self is None:
                    return None
                self.callback = callback
                return self

            def fire_(self, _timer):
                try:
                    self.callback()
                except Exception:
                    import traceback
                    traceback.print_exc()

        _CLASSES["ticker"] = IslandTicker
    return _CLASSES["ticker"]


class HoverGate:
    """Whether the island takes the mouse: not while Plip uses it, nor until the user moves it after."""

    def __init__(self) -> None:
        self.parked: tuple[float, float] | None = None
        self.until = 0.0

    def plip_moved(self, point: tuple[float, float], now: float, hold: float = YIELD_SECONDS) -> None:
        self.parked = point
        self.until = max(self.until, now + hold)

    def takes(self, mouse: tuple[float, float], over: bool, now: float) -> bool:
        if self.parked is not None:
            if now < self.until or math.dist(mouse, self.parked) <= PARK_SLOP:
                return False
            self.parked = None                     # the user moved it: theirs again
        return over


class IslandWindow:
    def __init__(self, on_command: Callable[[dict[str, Any]], None]):
        import AppKit

        self.on_command = on_command
        self.screen = notch_screen()
        self.geometry = notch_geometry(self.screen)
        self.island_size = (float(self.geometry["width"] + 76), float(self.geometry["height"]))
        rect = AppKit.NSMakeRect(0, 0, WIDTH, HEIGHT)
        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered, False)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setHasShadow_(False)
        panel.setLevel_(AppKit.NSMainMenuWindowLevel + 3)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorIgnoresCycle)
        panel.setHidesOnDeactivate_(False)
        panel.setReleasedWhenClosed_(False)
        panel.setIgnoresMouseEvents_(True)
        panel.setAcceptsMouseMovedEvents_(True)
        self.surface = WebSurface("island", rect, self._command)
        panel.setContentView_(self.surface.view)
        self.panel = panel
        self.place()
        panel.orderFrontRegardless()
        self._interactive = False
        self.gate = HoverGate()
        self._ticker = _ticker_class().alloc().initWithCallback_(self._tick)
        self._timer = AppKit.NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            1 / 30, self._ticker, "fire:", None, True)
        AppKit.NSRunLoop.mainRunLoop().addTimer_forMode_(self._timer, AppKit.NSRunLoopCommonModes)
        from mcp_vision.buddy.clear_path import on_clear

        self._stop_clearing = on_clear(self._clear)      # Plip's clicks pass through to the app below
        center = AppKit.NSNotificationCenter.defaultCenter()
        self._screens_changed = center.addObserverForName_object_queue_usingBlock_(
            AppKit.NSApplicationDidChangeScreenParametersNotification, None, None,
            lambda _note: self.place())

    def place(self) -> None:
        import AppKit

        self.screen = notch_screen()
        self.geometry = notch_geometry(self.screen)
        frame = self.screen.frame()
        x = float(frame.origin.x) + (float(frame.size.width) - WIDTH) / 2
        y = float(frame.origin.y) + float(frame.size.height) - HEIGHT
        self.panel.setFrame_display_(AppKit.NSMakeRect(x, y, WIDTH, HEIGHT), True)
        self.surface.post([{"type": "island", "state": {"notch": self.geometry}}])

    def post(self, messages: list[dict[str, Any]]) -> None:
        self.surface.post(messages)

    def _command(self, command: dict[str, Any]) -> None:
        if command["cmd"] == "island-rect":
            self.island_size = (float(command.get("width") or 0), float(command.get("height") or 0))
            return
        if command["cmd"] == "ready":
            self.surface.post([{"type": "island", "state": {"notch": self.geometry}}])
        self.on_command(command)

    # -- Plip's own clicks (clear_path) --------------------------------------------------------
    def _clear(self, points: list[tuple[float, float]], act: bool) -> None:
        """Any thread; returns once the island is out of the next click's way."""
        if not act or not points:
            return
        if threading.current_thread() is threading.main_thread():
            self.make_way(points)
            return
        from PyObjCTools import AppHelper

        done = threading.Event()

        def run() -> None:
            try:
                self.make_way(points)
            finally:
                done.set()
        AppHelper.callAfter(run)
        done.wait(0.3)

    def make_way(self, points: list[tuple[float, float]]) -> None:
        """Main thread: click-through for Plip's mouse; its parked cursor isn't a hover."""
        import AppKit

        primary = float(AppKit.NSScreen.screens()[0].frame().size.height)
        x, y = points[-1]
        self.gate.plip_moved((x, primary - y), time.monotonic())        # global top-left -> AppKit
        if self._interactive:
            self._interactive = False
            self.panel.setIgnoresMouseEvents_(True)
            self.surface.post([{"type": "island", "state": {"hovered": False}}])

    def _tick(self) -> None:
        import AppKit

        mouse = AppKit.NSEvent.mouseLocation()
        frame = self.panel.frame()
        over = island_hit((float(mouse.x), float(mouse.y)),
                          (float(frame.origin.x), float(frame.origin.y), float(frame.size.width), float(frame.size.height)),
                          self.island_size)
        inside = self.gate.takes((float(mouse.x), float(mouse.y)), over, time.monotonic())
        if inside != self._interactive:
            self._interactive = inside
            self.panel.setIgnoresMouseEvents_(not inside)
            self.surface.post([{"type": "island", "state": {"hovered": inside}}])

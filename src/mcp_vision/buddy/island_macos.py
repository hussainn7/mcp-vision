"""The Island: Plip's Dynamic-Island-style home in the MacBook notch.

A borderless, non-activating panel spans the top-center of the notched
display (or the main display on Macs without a notch, where it draws its own
pill). It floats above the menu bar on every Space and in full-screen apps,
stays out of screenshots, and passes clicks through everywhere except the
island's current shape, which the web UI reports as it animates.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp_vision.buddy.web_host import WebSurface

WIDTH, HEIGHT = 760.0, 340.0
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
        try:
            panel.setSharingType_(AppKit.NSWindowSharingNone)
        except Exception:
            pass
        self.surface = WebSurface("island", rect, self._command)
        panel.setContentView_(self.surface.view)
        self.panel = panel
        self.place()
        panel.orderFrontRegardless()
        self._interactive = False
        self._ticker = _ticker_class().alloc().initWithCallback_(self._tick)
        self._timer = AppKit.NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            1 / 30, self._ticker, "fire:", None, True)
        AppKit.NSRunLoop.mainRunLoop().addTimer_forMode_(self._timer, AppKit.NSRunLoopCommonModes)
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

    def _tick(self) -> None:
        import AppKit

        mouse = AppKit.NSEvent.mouseLocation()
        frame = self.panel.frame()
        inside = island_hit((float(mouse.x), float(mouse.y)),
                            (float(frame.origin.x), float(frame.origin.y), float(frame.size.width), float(frame.size.height)),
                            self.island_size)
        if inside != self._interactive:
            self._interactive = inside
            self.panel.setIgnoresMouseEvents_(not inside)
            self.surface.post([{"type": "island", "state": {"hovered": inside}}])

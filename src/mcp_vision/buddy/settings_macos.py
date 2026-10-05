"""Plip's settings / onboarding window (macOS)."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from mcp_vision.buddy.web_host import WebSurface

WIDTH, HEIGHT = 1000.0, 700.0


class SettingsWindow:
    def __init__(self, on_command: Callable[[dict[str, Any]], None]):
        import AppKit

        self.on_command = on_command
        self._tab = "home"
        style = (AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable
                 | AppKit.NSWindowStyleMaskMiniaturizable | AppKit.NSWindowStyleMaskResizable)
        rect = AppKit.NSMakeRect(0, 0, WIDTH, HEIGHT)
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, AppKit.NSBackingStoreBuffered, False)
        window.setTitle_("Plip")
        window.setTitlebarAppearsTransparent_(True)
        window.setTitleVisibility_(AppKit.NSWindowTitleHidden)
        window.setAppearance_(AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua))
        window.setBackgroundColor_(AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.024, 0.027, 0.039, 1))
        window.setMinSize_(AppKit.NSMakeSize(900, 600))
        window.setReleasedWhenClosed_(False)
        window.center()
        self.surface = WebSurface("settings", window.contentView().bounds(), self._command)
        window.setContentView_(self.surface.view)
        self.window = window

    def _command(self, command: dict[str, Any]) -> None:
        if command["cmd"] in {"ready", "settings-ready"}:
            self._go(self._tab)
        self.on_command(command)

    def _go(self, tab: str) -> None:
        self.surface.evaluate(f"location.hash = {json.dumps('#settings?tab=' + tab)}")

    def show(self, tab: str = "home") -> None:
        import AppKit

        self._tab = tab
        if self.surface.ready:
            self._go(tab)
        AppKit.NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    def front(self) -> None:
        """Bring the window back in front (after System Settings), on whatever it was showing."""
        import AppKit

        AppKit.NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    def post(self, messages: list[dict[str, Any]]) -> None:
        self.surface.post(messages)

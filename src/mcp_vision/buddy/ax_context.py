"""macOS screen context from the Accessibility tree (safe off the main thread).

Walks the frontmost app's menu bar and focused window breadth-first under a
node and time budget, keeping named, visible, interactive controls.
"""
from __future__ import annotations

import os
import time
from collections import deque
from typing import Any

from mcp_vision.buddy.ax_locator import _ROLE_NAMES, CONTROL_ROLES, _bounds, _copy, _name
from mcp_vision.buddy.screen_context import Control, ScreenContext

_SKIP_ROLES = {"AXStaticText", "AXImage", "AXRow", "AXCell"}     # too noisy for a map
_CONTAINER_ROLES = {"AXWindow", "AXGroup", "AXToolbar", "AXScrollArea", "AXSplitGroup", "AXTabGroup",
                    "AXMenuBar", "AXMenuBarItem", "AXWebArea", "AXLayoutArea", "AXSheet", "AXDrawer",
                    "AXList", "AXOutline", "AXTable", "AXRadioGroup", "AXBrowser", "AXUnknown", "AXSplitter"}


class MacAXContext:
    def __init__(self, *, node_cap: int = 1500, time_budget: float = 0.25, max_controls: int = 90):
        self.node_cap = node_cap
        self.time_budget = time_budget
        self.max_controls = max_controls

    def snapshot(self) -> ScreenContext:
        try:
            import ApplicationServices as AX
        except ImportError:
            return ScreenContext()
        if not AX.AXIsProcessTrusted():
            return ScreenContext()
        system = AX.AXUIElementCreateSystemWide()
        try:
            AX.AXUIElementSetMessagingTimeout(system, 0.05)
        except Exception:
            pass
        app = _copy(AX, system, "AXFocusedApplication")
        if app is None:
            return ScreenContext()
        try:
            err, pid = AX.AXUIElementGetPid(app, None)
            if err == 0 and int(pid) == os.getpid():
                return ScreenContext()        # never describe Blip's own windows
        except Exception:
            pass
        context = ScreenContext(app=str(_copy(AX, app, "AXTitle") or ""))
        window = _copy(AX, app, "AXFocusedWindow")
        if window is not None:
            context.window = str(_copy(AX, window, "AXTitle") or "")
        focused = _copy(AX, system, "AXFocusedUIElement")
        if focused is not None and _copy(AX, focused, "AXSubrole") != "AXSecureTextField":
            selected = _copy(AX, focused, "AXSelectedText")
            if isinstance(selected, str):
                context.selection = selected[:2000]
        roots = [root for root in (_copy(AX, app, "AXMenuBar"), window) if root is not None]
        context.controls = self._walk(AX, roots)
        return context

    def _walk(self, AX: Any, roots: list[Any]) -> list[Control]:
        deadline = time.monotonic() + self.time_budget
        queue = deque(roots)
        visited = 0
        controls: list[Control] = []
        while queue and visited < self.node_cap and len(controls) < self.max_controls:
            if time.monotonic() > deadline:
                break
            element = queue.popleft()
            visited += 1
            role = str(_copy(AX, element, "AXRole") or "")
            if (role in CONTROL_ROLES and role not in _SKIP_ROLES) or role == "AXMenuBarItem":
                bounds = _bounds(AX, element)
                name = _name(AX, element, role)
                if bounds and bounds.width > 0 and bounds.height > 0 and name:
                    x, y = bounds.center
                    controls.append(Control(label=name, role=_ROLE_NAMES.get(role, role.removeprefix("AX").lower()),
                                            x=x, y=y))
            if role in _CONTAINER_ROLES or role == "AXWindow" or not role:
                children = _copy(AX, element, "AXChildren") or []
                # Menus inside menu bar items are closed; don't descend into them.
                if role != "AXMenuBarItem":
                    queue.extend(list(children)[:200])
        return controls

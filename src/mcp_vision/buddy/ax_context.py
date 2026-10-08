"""macOS screen context from the Accessibility tree (safe off the main thread).

Walks the frontmost app's menu bar and focused window breadth-first under a
node and time budget, keeping named, visible, interactive controls.

Visible means inside the window and inside every scroll area and web page
around it. Apps still report what's scrolled away or hidden: Chrome keeps a
full-screen window's toolbar and tabs above the screen, and pins links that
scrolled off a page to the page's top edge as 1-2 pt slivers. Listed, those
filled the map and sent the model's points and clicks to the wrong place.
"""
from __future__ import annotations

import os
import time
from collections import deque
from typing import Any

from mcp_vision.buddy.ax_locator import _ROLE_NAMES, CONTROL_ROLES, _bounds, _copy, _name
from mcp_vision.buddy.geometry import Rect
from mcp_vision.buddy.screen_context import SELECTION_LIMIT, Control, ScreenContext

_SKIP_ROLES = {"AXStaticText", "AXImage", "AXRow", "AXCell"}     # too noisy for a map
_CONTAINER_ROLES = {"AXWindow", "AXGroup", "AXToolbar", "AXScrollArea", "AXSplitGroup", "AXTabGroup",
                    "AXMenuBar", "AXMenuBarItem", "AXWebArea", "AXLayoutArea", "AXSheet", "AXDrawer",
                    "AXList", "AXOutline", "AXTable", "AXRadioGroup", "AXBrowser", "AXUnknown", "AXSplitter"}
# What content is cut to: a window, and the viewport of a scroll area or web page inside it.
_CLIP_ROLES = {"AXWindow", "AXScrollArea", "AXWebArea"}
MIN_VISIBLE = 4.0                     # points: thinner than this on screen isn't something to point at


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
                return ScreenContext()        # never describe Plip's own windows
        except Exception:
            pass
        context = ScreenContext(app=str(_copy(AX, app, "AXTitle") or ""))
        window = _copy(AX, app, "AXFocusedWindow")
        if window is not None:
            context.window = str(_copy(AX, window, "AXTitle") or "")
        focused = _copy(AX, system, "AXFocusedUIElement")
        if focused is not None:
            context.focused = _focused(AX, focused)
            if _copy(AX, focused, "AXSubrole") != "AXSecureTextField":
                selected = _copy(AX, focused, "AXSelectedText")
                if isinstance(selected, str):
                    context.selection, context.selection_chars = selected[:SELECTION_LIMIT], len(selected)
        roots = [root for root in (_copy(AX, app, "AXMenuBar"), window) if root is not None]
        context.controls = self._walk(AX, roots)
        return context

    def _walk(self, AX: Any, roots: list[Any]) -> list[Control]:
        deadline = time.monotonic() + self.time_budget
        queue: deque[tuple[Any, Rect | None]] = deque((root, None) for root in roots)
        visited = 0
        controls: list[Control] = []
        seen: set[tuple[str, str, int, int]] = set()
        while queue and visited < self.node_cap and len(controls) < self.max_controls:
            if time.monotonic() > deadline:
                break
            element, clip = queue.popleft()
            visited += 1
            role = str(_copy(AX, element, "AXRole") or "")
            if role in _CLIP_ROLES:
                bounds = _bounds(AX, element)
                if bounds is not None:
                    clip = bounds if clip is None else clip.intersect(bounds)
                    if clip is None:
                        continue                  # scrolled away or hidden: nothing inside it is on screen
            if (role in CONTROL_ROLES and role not in _SKIP_ROLES) or role == "AXMenuBarItem":
                bounds = _bounds(AX, element)
                shown = bounds if bounds is None or clip is None else bounds.intersect(clip)
                name = _name(AX, element, role) if shown is not None else ""
                if shown is not None and shown.width >= MIN_VISIBLE and shown.height >= MIN_VISIBLE and name:
                    x, y = shown.center           # the part on screen, so a half-scrolled control is still hit
                    kind = _ROLE_NAMES.get(role, role.removeprefix("AX").lower())
                    key = (name, kind, round(x), round(y))
                    if key not in seen:           # Chrome lists its tab strip twice
                        seen.add(key)
                        controls.append(Control(label=name, role=kind, x=x, y=y))
            if role in _CONTAINER_ROLES or role == "AXWindow" or not role:
                children = _copy(AX, element, "AXChildren") or []
                # Menus inside menu bar items are closed; don't descend into them.
                if role != "AXMenuBarItem":
                    queue.extend((child, clip) for child in list(children)[:200])
        return controls


_TYPING_ROLES = {"AXTextField", "AXTextArea", "AXSearchField", "AXComboBox", "AXSecureTextField"}


def _focused(AX: Any, element: Any) -> str:
    """The text box typing goes into right now, like 'search field "Search mail"' ("" when none)."""
    role = str(_copy(AX, element, "AXRole") or "")
    if role not in _TYPING_ROLES:
        return ""
    secure = role == "AXSecureTextField" or _copy(AX, element, "AXSubrole") == "AXSecureTextField"
    kind = "password field" if secure else _ROLE_NAMES.get(role, role.removeprefix("AX").lower())
    name = " ".join(_name(AX, element, role).split())[:60]      # its label, never what's typed in it
    return f'{kind} "{name}"' if name else kind

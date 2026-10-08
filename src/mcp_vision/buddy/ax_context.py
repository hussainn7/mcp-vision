"""macOS screen context from the Accessibility tree (safe off the main thread).

Walks the frontmost app's open menus and focused window breadth-first under a
node and time budget, keeping named, visible, interactive controls and the
visible text, then its menu bar on its own (listed last, so a busy page never
crowds it out and it never crowds the page).

Visible means inside the window and inside every scroll area and web page
around it. Apps still report what's scrolled away or hidden: Chrome keeps a
full-screen window's toolbar and tabs above the screen, and pins links that
scrolled off a page to the page's top edge as 1-2 pt slivers. Listed, those
filled the map and sent the model's points and clicks to the wrong place.

Chromium and Electron apps only build the tree for their pages once asked
(``AXManualAccessibility``, or the older VoiceOver switch for browsers that
refuse it). Until the page shows up the map says it's blind, so the step
works from the screenshot instead of a menu bar posing as the page.
"""
from __future__ import annotations

import os
import time
from collections import deque
from typing import Any

from mcp_vision.buddy.ax_locator import _ROLE_NAMES, CONTROL_ROLES, _bounds, _copy, _name
from mcp_vision.buddy.geometry import Rect
from mcp_vision.buddy.screen_context import SELECTION_LIMIT, Control, ScreenContext, flatten_page

_SKIP_ROLES = {"AXStaticText", "AXImage", "AXRow", "AXCell"}     # too noisy for a map
_CONTAINER_ROLES = {"AXWindow", "AXGroup", "AXToolbar", "AXScrollArea", "AXSplitGroup", "AXTabGroup",
                    "AXMenuBar", "AXMenuBarItem", "AXWebArea", "AXLayoutArea", "AXSheet", "AXDrawer",
                    "AXList", "AXOutline", "AXTable", "AXRadioGroup", "AXBrowser", "AXUnknown", "AXSplitter",
                    "AXMenu"}
# What content is cut to: a window, and the viewport of a scroll area or web page inside it.
_CLIP_ROLES = {"AXWindow", "AXScrollArea", "AXWebArea"}
MIN_VISIBLE = 4.0                     # points: thinner than this on screen isn't something to point at
# Chromium browsers that ignore AXManualAccessibility still honor the older VoiceOver switch. By bundle id:
# names drift (Chrome 154 calls itself "Chrome", not "Google Chrome").
_CHROMIUM_IDS = {"com.google.Chrome", "com.google.Chrome.beta", "com.google.Chrome.dev", "com.google.Chrome.canary",
                 "org.chromium.Chromium", "com.brave.Browser", "com.microsoft.edgemac", "company.thebrowser.Browser",
                 "company.thebrowser.dia", "com.vivaldi.Vivaldi", "com.operasoftware.Opera", "ai.perplexity.comet"}
_CHROMIUM = {"Google Chrome", "Chrome", "Chromium", "Brave Browser", "Microsoft Edge", "Arc", "Vivaldi", "Opera", "Dia",
             "Comet", "Google Chrome Canary"}
_WEB_IDS = {"com.apple.Safari", "com.apple.SafariTechnologyPreview", "org.mozilla.firefox"}
ASK_AGAIN = 3.0               # seconds between asks while an app's page still isn't in the map


class MacAXContext:
    def __init__(self, *, node_cap: int = 1800, time_budget: float = 0.3, max_controls: int = 90,
                 max_texts: int = 60):
        self.node_cap = node_cap
        self.time_budget = time_budget
        self.max_controls = max_controls
        self.max_texts = max_texts
        self._asked: dict[int, float] = {}  # pid -> when Plip last asked it to show its web content
        self._exposed: set[int] = set()     # pids whose page has shown up in the map: no need to ask again
        self._kinds: dict[int, tuple[str, bool]] = {}

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
        if app is None or _pid(AX, app) == os.getpid():
            return ScreenContext()            # nothing in front, or Plip's own windows: never describe those
        return self._snapshot(AX, system, app)

    def _snapshot(self, AX: Any, system: Any, app: Any) -> ScreenContext:
        context = ScreenContext(app=str(_copy(AX, app, "AXTitle") or ""))
        pid = _pid(AX, app)
        asked = self._expose_web_content(AX, app, pid, context.app)
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
        roots: list[Any] = []
        # An open context menu or dropdown list is the app's own child, not the window's: list it first.
        for child in list(_copy(AX, app, "AXChildren") or [])[:20]:
            if _copy(AX, child, "AXRole") == "AXMenu":
                roots.append(child)
        if window is not None:
            roots.append(window)
        web = self._fill(AX, context, roots, app)
        if web:
            self._exposed.add(pid)
        elif window is not None and self._web_app(pid, context.app):
            if asked:                                    # just switched on: the page builds its tree in a beat
                time.sleep(0.2)
                web = self._fill(AX, context, roots, app)
                if web:
                    self._exposed.add(pid)
            context.blind = not web
        return context

    def _fill(self, AX: Any, context: ScreenContext, roots: list[Any], app: Any) -> bool:
        context.controls, context.texts, context.scroll_areas, context.url, web = self._walk_full(AX, roots)
        menu_bar = _copy(AX, app, "AXMenuBar")
        if menu_bar is not None:                         # walked on its own: a busy page never crowds it out
            context.controls += self._walk_full(AX, [menu_bar])[0]
        return web

    def _kind(self, pid: int) -> tuple[str, bool]:
        """(bundle id, is it an Electron app) for a running app."""
        if pid not in self._kinds:
            bundle, electron = "", False
            try:
                import AppKit

                running = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
                if running is not None:
                    bundle = str(running.bundleIdentifier() or "")
                    path = str(running.bundleURL().path()) if running.bundleURL() is not None else ""
                    electron = bool(path) and os.path.isdir(
                        os.path.join(path, "Contents", "Frameworks", "Electron Framework.framework"))
            except Exception:
                pass
            self._kinds[pid] = (bundle, electron)
        return self._kinds[pid]

    def _web_app(self, pid: int, name: str) -> bool:
        """Does this app show its content as a web page (so a map without one is blind)?"""
        bundle, electron = self._kind(pid)
        return electron or bundle in _CHROMIUM_IDS or bundle in _WEB_IDS or name in _CHROMIUM

    def _expose_web_content(self, AX: Any, app: Any, pid: int, name: str) -> bool:
        """Ask Chromium/Electron apps to build the Accessibility tree for their pages. True: just asked."""
        now = time.monotonic()
        if pid < 0 or pid in self._exposed or now - self._asked.get(pid, -ASK_AGAIN) < ASK_AGAIN:
            return False
        self._asked[pid] = now
        try:
            status = AX.AXUIElementSetAttributeValue(app, "AXManualAccessibility", True)
        except Exception:
            status = -1
        if status != 0 and (self._kind(pid)[0] in _CHROMIUM_IDS or name in _CHROMIUM):
            try:
                AX.AXUIElementSetAttributeValue(app, "AXEnhancedUserInterface", True)
            except Exception:
                pass
        return True

    def read(self, *, node_cap: int = 6000, time_budget: float = 1.5) -> list[str]:
        """Every text on the frontmost page or window, scrolled-out parts too, in reading order, as lines."""
        try:
            import ApplicationServices as AX
        except ImportError:
            return []
        if not AX.AXIsProcessTrusted():
            return []
        system = AX.AXUIElementCreateSystemWide()
        try:
            AX.AXUIElementSetMessagingTimeout(system, 0.25)     # a hung app can't hold up the read
        except Exception:
            pass
        app = _copy(AX, system, "AXFocusedApplication")
        if app is None or _pid(AX, app) == os.getpid():
            return []
        self._expose_web_content(AX, app, _pid(AX, app), str(_copy(AX, app, "AXTitle") or ""))
        window = _copy(AX, app, "AXFocusedWindow")
        if window is None:
            return []
        root = _find_role(AX, window, "AXWebArea") or window
        deadline = time.monotonic() + time_budget
        stack, items, visited = [root], [], 0
        while stack and visited < node_cap and time.monotonic() < deadline:
            element = stack.pop()
            visited += 1
            role = str(_copy(AX, element, "AXRole") or "")
            if role in {"AXHeading", "AXLink", "AXRow", "AXButton"}:
                items.append(("break", ""))
            if role == "AXStaticText":
                value = _copy(AX, element, "AXValue")
                if isinstance(value, str) and value.strip():
                    items.append(("text", value))
                continue
            if role in {"AXTextField", "AXTextArea", "AXSecureTextField", "AXImage", "AXMenuBar"}:
                continue                                # what's typed in boxes isn't the page's to read out
            children = list(_copy(AX, element, "AXChildren") or [])
            if role == "AXButton" and not children:
                continue                                # a plain button ("Close", "Menu"): noise for reading
            if not children and role in {"AXHeading", "AXLink"}:
                own = _copy(AX, element, "AXTitle") or _copy(AX, element, "AXValue")
                if isinstance(own, str) and own.strip():
                    items.append(("text", own))
            stack.extend(reversed(children[:400]))
        return flatten_page(items)

    def _walk(self, AX: Any, roots: list[Any]) -> list[Control]:
        return self._walk_full(AX, roots)[0]

    def _walk_full(self, AX: Any, roots: list[Any]
                   ) -> tuple[list[Control], list[Control], list[Control], str, bool]:
        """Breadth-first over (element, clip) pairs: controls, visible text, scroll areas, the page's address,
        and whether a web page was in there (the map isn't blind to it)."""
        deadline = time.monotonic() + self.time_budget
        queue: deque[tuple[Any, Rect | None]] = deque((root, None) for root in roots)
        visited = 0
        controls: list[Control] = []
        texts: list[Control] = []
        areas: list[Control] = []
        url, web = "", False
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
                    inside = bounds if clip is None else clip.intersect(bounds)
                    if inside is None:
                        continue                  # scrolled away or hidden: nothing inside it is on screen
                    if role != "AXWindow" and len(areas) < 6 and bounds.width > 80 and bounds.height > 80:
                        name = _copy(AX, element, "AXDescription") or _copy(AX, element, "AXTitle") or \
                            ("page" if role == "AXWebArea" else "scroll area")
                        where = _scrolled(AX, element) if role == "AXScrollArea" else ""
                        x, y = inside.center
                        areas.append(Control(label=str(name)[:40] + where, role="page" if role == "AXWebArea"
                                             else "scroll area", x=x, y=y))
                    clip = inside
                if role == "AXWebArea":
                    web = True
                    url = url or _url(_copy(AX, element, "AXURL"))
            elif clip is not None and role in _CONTAINER_ROLES:
                bounds = _bounds(AX, element)
                if bounds is not None and bounds.width > 0 and bounds.height > 0 and clip.intersect(bounds) is None:
                    continue                      # off screen (a long page's lower half): skip the whole subtree
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
            elif role in {"AXStaticText", "AXHeading"} and len(texts) < self.max_texts:
                bounds = _bounds(AX, element)
                value = _copy(AX, element, "AXValue") or _copy(AX, element, "AXTitle")
                shown = bounds if bounds is None or clip is None else bounds.intersect(clip)
                if shown is not None and shown.width >= MIN_VISIBLE and shown.height >= MIN_VISIBLE \
                        and isinstance(value, str) and value.strip():
                    x, y = shown.center
                    texts.append(Control(label=value.strip()[:200], role="text", x=x, y=y))
            if role in _CONTAINER_ROLES or role in {"AXCell", "AXRow"} or not role:
                # A menu bar item's menu is closed unless it's the one open now.
                if role == "AXMenuBarItem" and not _copy(AX, element, "AXSelected"):
                    continue
                children = _copy(AX, element, "AXChildren") or []
                queue.extend((child, clip) for child in list(children)[:200])
        return controls, texts, areas, url, web


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


def _pid(AX: Any, element: Any) -> int:
    try:
        err, pid = AX.AXUIElementGetPid(element, None)
        return int(pid) if err == 0 else -1
    except Exception:
        return -1


def _find_role(AX: Any, root: Any, wanted: str, cap: int = 400) -> Any:
    """The first element with this role under ``root`` (breadth-first), or None."""
    queue, visited = deque([root]), 0
    while queue and visited < cap:
        element = queue.popleft()
        visited += 1
        if _copy(AX, element, "AXRole") == wanted:
            return element
        queue.extend(list(_copy(AX, element, "AXChildren") or [])[:100])
    return None


def _scrolled(AX: Any, area: Any) -> str:
    """Where a scroll area is scrolled to, from its vertical scroll bar: " (at the top)", " (40% down)"."""
    bar = _copy(AX, area, "AXVerticalScrollBar")
    value = _copy(AX, bar, "AXValue") if bar is not None else None
    if not isinstance(value, (int, float)):
        return ""
    return describe_scroll(float(value))


def describe_scroll(fraction: float) -> str:
    if fraction <= 0.01:
        return " (at the top)"
    if fraction >= 0.99:
        return " (scrolled to the bottom)"
    return f" (scrolled {round(fraction * 100)}% down)"


def _url(value: Any) -> str:
    """An AXURL (NSURL or string) as text; only web addresses."""
    if value is None:
        return ""
    try:
        text = str(value.absoluteString()) if hasattr(value, "absoluteString") else str(value)
    except Exception:
        return ""
    return text if text.startswith(("http://", "https://")) else ""

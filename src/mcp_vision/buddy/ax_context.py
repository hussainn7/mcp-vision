"""macOS screen context from the Accessibility tree (safe off the main thread).

BFS over open menus and the focused window, clipped to what's really visible, then the menu bar.
Chromium/Electron pages appear only once asked (``AXManualAccessibility``); till then the map is blind.
"""
from __future__ import annotations

import os
import re
import time
from collections import deque
from typing import Any

from mcp_vision.buddy.ax_locator import _ROLE_NAMES, CONTROL_ROLES, _bounds, _copy, _name
from mcp_vision.buddy.geometry import Rect
from mcp_vision.buddy.screen_context import (
    SELECTION_LIMIT, Control, ScreenContext, flatten_page, looks_secret,
)

_SKIP_ROLES = {"AXStaticText", "AXImage", "AXRow", "AXCell"}     # too noisy for a map
_CONTAINER_ROLES = {"AXWindow", "AXGroup", "AXToolbar", "AXScrollArea", "AXSplitGroup", "AXTabGroup",
                    "AXMenuBar", "AXMenuBarItem", "AXWebArea", "AXLayoutArea", "AXSheet", "AXDrawer",
                    "AXList", "AXOutline", "AXTable", "AXRadioGroup", "AXBrowser", "AXUnknown", "AXSplitter",
                    "AXMenu"}
# roles whose bounds clip their content
_CLIP_ROLES = {"AXWindow", "AXScrollArea", "AXWebArea"}
MIN_VISIBLE = 4.0                     # pt: thinner isn't pointable
_TYPED_ROLES = {"AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"}     # boxes you type in
_VALUE_KEPT = 400             # chars of a box's value kept (a text area's last)
_VALUE_MAX = 4000             # longer = a document/scrollback: not copied
# Chromium bundle ids (names drift); these honor the older VoiceOver switch
_CHROMIUM_IDS = {"com.google.Chrome", "com.google.Chrome.beta", "com.google.Chrome.dev", "com.google.Chrome.canary",
                 "org.chromium.Chromium", "com.brave.Browser", "com.microsoft.edgemac", "company.thebrowser.Browser",
                 "company.thebrowser.dia", "com.vivaldi.Vivaldi", "com.operasoftware.Opera", "ai.perplexity.comet"}
_CHROMIUM = {"Google Chrome", "Chrome", "Chromium", "Brave Browser", "Microsoft Edge", "Arc", "Vivaldi", "Opera", "Dia",
             "Comet", "Google Chrome Canary"}
_WEB_IDS = {"com.apple.Safari", "com.apple.SafariTechnologyPreview", "org.mozilla.firefox"}
ASK_AGAIN = 3.0               # s between asks while a page is still missing


class MacAXContext:
    def __init__(self, *, node_cap: int = 1800, time_budget: float = 0.3, max_controls: int = 90,
                 max_texts: int = 60):
        self.node_cap = node_cap
        self.time_budget = time_budget
        self.max_controls = max_controls
        self.max_texts = max_texts
        self._asked: dict[int, float] = {}  # pid -> last ask to expose web content
        self._exposed: set[int] = set()     # pids whose page showed up: don't ask again
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
            return ScreenContext()            # nothing in front, or Plip's own windows
        return self._snapshot(AX, system, app)

    def _snapshot(self, AX: Any, system: Any, app: Any) -> ScreenContext:
        context = ScreenContext(app=str(_copy(AX, app, "AXTitle") or ""))
        pid = _pid(AX, app)
        asked = self._expose_web_content(AX, app, pid, context.app)
        window = _copy(AX, app, "AXFocusedWindow")
        if window is not None:
            context.window = str(_copy(AX, window, "AXTitle") or "")
            context.window_frame = _bounds(AX, window)
        focused = _copy(AX, system, "AXFocusedUIElement")
        if focused is not None:
            context.focused = _focused(AX, focused)
            if _copy(AX, focused, "AXSubrole") != "AXSecureTextField":
                selected = _copy(AX, focused, "AXSelectedText")
                if isinstance(selected, str):
                    context.selection, context.selection_chars = selected[:SELECTION_LIMIT], len(selected)
        roots: list[Any] = []
        # open menus/dropdowns hang off the app, not the window
        for child in list(_copy(AX, app, "AXChildren") or [])[:20]:
            if _copy(AX, child, "AXRole") == "AXMenu":
                roots.append(child)
        if window is not None:
            roots.append(window)
        web = self._fill(AX, context, roots, app)
        if web:
            self._exposed.add(pid)
        elif window is not None and self._web_app(pid, context.app):
            if asked:                                    # just asked: the tree builds in a beat
                time.sleep(0.2)
                web = self._fill(AX, context, roots, app)
                if web:
                    self._exposed.add(pid)
            context.blind = not web
        return context

    def _fill(self, AX: Any, context: ScreenContext, roots: list[Any], app: Any) -> bool:
        context.controls, context.texts, context.scroll_areas, context.url, web = self._walk_full(AX, roots)
        menu_bar = _copy(AX, app, "AXMenuBar")
        if menu_bar is not None:                         # own walk: a busy page can't crowd it out
            context.controls += self._walk_full(AX, [menu_bar])[0]
        return web

    def _kind(self, pid: int) -> tuple[str, bool]:
        """(bundle id, is Electron) for a pid."""
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
        """Is its content a web page (so a map without one is blind)?"""
        bundle, electron = self._kind(pid)
        return electron or bundle in _CHROMIUM_IDS or bundle in _WEB_IDS or name in _CHROMIUM

    def _expose_web_content(self, AX: Any, app: Any, pid: int, name: str) -> bool:
        """Ask Chromium/Electron apps to build their page's AX tree; True if just asked."""
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
        """All text on the frontmost page/window, off-screen too, as lines in reading order."""
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
                continue                                # typed text isn't page text
            children = list(_copy(AX, element, "AXChildren") or [])
            if role == "AXButton" and not children:
                continue                                # plain button ("Close"): noise
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
        """BFS over (element, clip): controls, texts, scroll areas, page url, and whether a web page was seen."""
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
                        continue                  # scrolled away or hidden
                    if role != "AXWindow" and len(areas) < 6 and bounds.width > 80 and bounds.height > 80:
                        name = _copy(AX, element, "AXDescription") or _copy(AX, element, "AXTitle") or \
                            ("page" if role == "AXWebArea" else "scroll area")
                        where = _scrolled(AX, element) if role == "AXScrollArea" else ""
                        x, y = inside.center
                        areas.append(Control(label=str(name)[:40] + where, role="page" if role == "AXWebArea"
                                             else "scroll area", x=x, y=y, w=inside.width, h=inside.height))
                    clip = inside
                if role == "AXWebArea":
                    web = True
                    url = url or _url(_copy(AX, element, "AXURL"))
            elif clip is not None and role in _CONTAINER_ROLES:
                bounds = _bounds(AX, element)
                if bounds is not None and bounds.width > 0 and bounds.height > 0 and clip.intersect(bounds) is None:
                    continue                      # off screen: skip the subtree
            if (role in CONTROL_ROLES and role not in _SKIP_ROLES) or role == "AXMenuBarItem":
                bounds = _bounds(AX, element)
                shown = bounds if bounds is None or clip is None else bounds.intersect(clip)
                typed = role in _TYPED_ROLES
                # a box goes by title/placeholder, never its value
                name = "" if shown is None else _box_name(AX, element, role) if typed else _name(AX, element, role)
                if shown is not None and shown.width >= MIN_VISIBLE and shown.height >= MIN_VISIBLE and name:
                    x, y = shown.center           # visible part: half-scrolled controls still hit
                    kind = _ROLE_NAMES.get(role, role.removeprefix("AX").lower())
                    key = (name, kind, round(x), round(y))
                    if key not in seen:           # Chrome lists its tab strip twice
                        seen.add(key)
                        secure = typed and _copy(AX, element, "AXSubrole") == "AXSecureTextField"
                        controls.append(Control(label=name, role=kind, x=x, y=y, w=shown.width, h=shown.height,
                                                secure=secure, value=_typed(AX, element, name, role)
                                                if typed and not secure else ""))
            elif role in {"AXStaticText", "AXHeading"} and len(texts) < self.max_texts:
                bounds = _bounds(AX, element)
                value = _copy(AX, element, "AXValue") or _copy(AX, element, "AXTitle")
                shown = bounds if bounds is None or clip is None else bounds.intersect(clip)
                if shown is not None and shown.width >= MIN_VISIBLE and shown.height >= MIN_VISIBLE \
                        and isinstance(value, str) and value.strip():
                    x, y = shown.center
                    texts.append(Control(label=value.strip()[:200], role="text", x=x, y=y, w=shown.width,
                                         h=shown.height))
            if role in _CONTAINER_ROLES or role in {"AXCell", "AXRow"} or not role:
                # only the open menu bar item's menu
                if role == "AXMenuBarItem" and not _copy(AX, element, "AXSelected"):
                    continue
                children = _copy(AX, element, "AXChildren") or []
                queue.extend((child, clip) for child in list(children)[:200])
        return controls, texts, areas, url, web


def _box_name(AX: Any, element: Any, role: str) -> str:
    """Title or placeholder, never the value; unnamed gets its role, unless it's a long text area
    (typing by number there selects all and overwrites it)."""
    placeholder = _copy(AX, element, "AXPlaceholderValue")
    name = _name(AX, element, "") or (placeholder.strip() if isinstance(placeholder, str) else "")    # not its value
    if name or (role == "AXTextArea" and _length(AX, element) > _VALUE_KEPT):
        return name
    return _ROLE_NAMES.get(role, role.removeprefix("AX").lower())


def _length(AX: Any, element: Any) -> int:
    """Chars in a text box, without copying it when possible (unknown: a lot)."""
    size = _copy(AX, element, "AXNumberOfCharacters")
    if isinstance(size, (int, float)):
        return int(size)
    value = _copy(AX, element, "AXValue")
    return len(value) if isinstance(value, str) else _VALUE_MAX + 1


def _typed(AX: Any, element: Any, name: str, role: str) -> str:
    """What's typed in a box (a text area's end), so the model sees it landed; "" for secrets or huge text."""
    if looks_secret(name):
        return ""
    size = _copy(AX, element, "AXNumberOfCharacters")
    if isinstance(size, (int, float)) and size > _VALUE_MAX:
        return ""
    value = _copy(AX, element, "AXValue")
    if not isinstance(value, str) or len(value) > _VALUE_MAX:
        return ""
    value = value.strip()
    return value[-_VALUE_KEPT:] if role == "AXTextArea" else value[:_VALUE_KEPT]


_MATCH_ROLES = {"AXStaticText", "AXHeading", "AXLink", "AXButton", "AXTab", "AXMenuItem", "AXCheckBox",
                "AXRadioButton", "AXPopUpButton", "AXMenuButton", "AXCell", "AXDisclosureTriangle"}
_TEXT_BOXES = {"AXTextField", "AXTextArea", "AXSecureTextField", "AXSearchField", "AXComboBox"}


def find_text(AX: Any, window: Any, text: str, *, node_cap: int = 6000, time_budget: float = 1.0) -> Any:
    """Element showing ``text`` on the page, off-screen too (exact > prefix > word > substring; no text boxes)."""
    needle = " ".join(text.lower().split())
    if not needle or window is None:
        return None
    word = re.compile(rf"(?<!\w){re.escape(needle)}(?!\w)")
    deadline = time.monotonic() + time_budget
    stack, visited, best, found = [_find_role(AX, window, "AXWebArea") or window], 0, 4, None
    while stack and visited < node_cap and time.monotonic() < deadline:
        element = stack.pop()
        visited += 1
        role = str(_copy(AX, element, "AXRole") or "")
        if role in _TEXT_BOXES:
            continue
        if role in _MATCH_ROLES:
            for attribute in ("AXValue", "AXTitle", "AXDescription"):
                value = _copy(AX, element, attribute)
                label = " ".join(value.lower().split()) if isinstance(value, str) else ""
                if needle in label:
                    rank = 0 if label == needle else 1 if label.startswith(needle) else 2 if word.search(label) else 3
                    if rank < best:
                        best, found = rank, element
                    break
            if best == 0:
                return found
        stack.extend(reversed(list(_copy(AX, element, "AXChildren") or [])[:400]))
    return found


def visible_spot(AX: Any, element: Any, window: Rect | None, depth: int = 40
                 ) -> tuple[tuple[float, float] | None, Rect | None]:
    """(centre, rect) to wheel at for an off-screen element: its nearest visible ancestor, clipped by every
    panel around it (catches unlabeled overflow divs). (None, None) if none."""
    boxes, node = [], _copy(AX, element, "AXParent")
    while node is not None and len(boxes) < depth and _copy(AX, node, "AXRole") not in {"AXWindow", "AXApplication"}:
        boxes.append(_bounds(AX, node))
        node = _copy(AX, node, "AXParent")
    real = [box if box is not None and box.width > 0 and box.height > 0 else None for box in boxes]
    for index, box in enumerate(real):
        if box is None:
            continue
        view = box if window is None else box.intersect(window)
        for outer in real[index + 1:]:
            if outer is not None and view is not None:
                view = view.intersect(outer)          # clipped by every panel around it
        if view is not None and view.width >= 40 and view.height >= 40:
            return view.center, view
    return None, None


_TYPING_ROLES = {"AXTextField", "AXTextArea", "AXSearchField", "AXComboBox", "AXSecureTextField"}


def _focused(AX: Any, element: Any) -> str:
    """Focused text box, like 'search field "Search mail"', or ""."""
    role = str(_copy(AX, element, "AXRole") or "")
    if role not in _TYPING_ROLES:
        return ""
    secure = role == "AXSecureTextField" or _copy(AX, element, "AXSubrole") == "AXSecureTextField"
    kind = "password field" if secure else _ROLE_NAMES.get(role, role.removeprefix("AX").lower())
    name = " ".join(_name(AX, element, role).split())[:60]      # label, never the value
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
    """Scroll position from the vertical bar, like " (40% down)"."""
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

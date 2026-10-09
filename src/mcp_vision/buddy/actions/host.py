"""Platform work behind actions.

``MacHost`` uses the tools macOS already ships: ``open``, ``osascript``,
``mdfind`` (Spotlight), ``shortcuts`` and Quartz/Accessibility for typing.
``PortableHost`` covers the same surface with plain Python where it can
(file search, opening files) so headless runs and tests behave the same.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from mcp_vision.buddy.clear_path import clear
from mcp_vision.buddy.geometry import Rect

APP_DIRS = ("/Applications", "/Applications/Utilities", "/System/Applications",
            "/System/Applications/Utilities", "~/Applications", "/System/Library/CoreServices")
SEARCH_ROOTS = ("Desktop", "Documents", "Downloads", "Pictures", "Movies", "Music")
SKIP_PARTS = {"Library", "node_modules", ".git", ".Trash", "__pycache__", ".venv", "venv"}
KIND_EXTENSIONS = {
    "pdf": {".pdf"},
    "image": {".png", ".jpg", ".jpeg", ".heic", ".gif", ".webp", ".tiff", ".svg"},
    "document": {".pdf", ".doc", ".docx", ".pages", ".txt", ".rtf", ".md", ".key", ".ppt", ".pptx",
                 ".numbers", ".xls", ".xlsx", ".csv"},
    "video": {".mov", ".mp4", ".m4v", ".avi", ".mkv"},
    "audio": {".mp3", ".wav", ".m4a", ".aiff", ".flac"},
    "archive": {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".dmg"},
}
SPOTLIGHT_KINDS = {"pdf": "com.adobe.pdf", "image": "public.image", "video": "public.movie",
                   "audio": "public.audio", "archive": "public.archive", "document": "public.content"}


KEY_CODES = {
    "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "backspace": 51, "escape": 53, "esc": 53,
    "forwarddelete": 117, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100,
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12,
    "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23,
    "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34,
    "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46,
    ".": 47, "`": 50,
}
KEY_ALIASES = {"plus": "=", "minus": "-", "zoomin": "=", "zoomout": "-"}
MODIFIER_NAMES = {"cmd": "cmd", "command": "cmd", "⌘": "cmd", "shift": "shift", "⇧": "shift", "alt": "alt",
                  "option": "alt", "opt": "alt", "⌥": "alt", "ctrl": "ctrl", "control": "ctrl", "⌃": "ctrl"}


def parse_keys(keys: str, masks: dict[str, int] | None = None) -> tuple[int, int]:
    """"cmd+shift+t" -> (key code, modifier flags). "cmd++" and "cmd+plus" both zoom in. Raises ValueError."""
    masks = masks or {"cmd": 1 << 20, "shift": 1 << 17, "alt": 1 << 19, "ctrl": 1 << 18}
    compact = keys.replace(" ", "").lower()
    if compact.endswith("++"):
        compact = compact[:-1] + "="                  # "cmd++" means the + key, which is = on the keyboard
    parts = [part for part in compact.split("+") if part]
    if not parts:
        raise ValueError("no key")
    flags = 0
    for part in parts[:-1]:
        name = MODIFIER_NAMES.get(part)
        if name is None:
            raise ValueError(f"unknown modifier {part}")
        flags |= masks[name]
    key = KEY_ALIASES.get(parts[-1], parts[-1])
    if key not in KEY_CODES:
        raise ValueError(f"unknown key {key}")
    return KEY_CODES[key], flags


class NotSupported(RuntimeError):
    pass


@dataclass(frozen=True)
class FileHit:
    path: str
    modified: float

    def as_item(self, home: str) -> dict:
        shown = self.path.replace(home, "~", 1) if self.path.startswith(home + os.sep) else self.path
        return {"title": os.path.basename(self.path), "detail": os.path.dirname(shown),
                "path": self.path, "modified": int(self.modified)}


@dataclass(frozen=True)
class Reveal:
    """Result of ``scroll_to_visible``."""

    found: bool = False                                 # on the page, maybe off screen
    asked: bool = False                                 # AXScrollToVisible taken (or still running)
    scroller: tuple[float, float] | None = None         # visible spot in its panel: wheel there
    direction: str = ""                                 # "down" / "up" from that spot
    at: tuple[float, float] | None = None               # already in view: its center, global points


def run(argv: list[str], timeout: float = 10.0, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, input=input_text,
                          stdin=None if input_text is not None else subprocess.DEVNULL)


def applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


class PortableHost:
    """Works anywhere; macOS-only features raise ``NotSupported``."""

    name = "portable"

    def __init__(self, home: str | None = None):
        self.home = os.path.expanduser(home or "~")
        self._apps: dict[str, str] | None = None

    # apps & urls -------------------------------------------------------------------
    def list_apps(self) -> dict[str, str]:
        """Lower-cased app name -> launch path."""
        if self._apps is None:
            self._apps = {}
            for directory in APP_DIRS:
                folder = Path(os.path.expanduser(directory))
                if folder.is_dir():
                    for entry in folder.glob("*.app"):
                        self._apps.setdefault(entry.stem.lower(), str(entry))
        return self._apps

    def open_app(self, path: str) -> None:
        raise NotSupported("Opening apps works on macOS.")

    def open(self, target: str) -> None:
        opener = shutil.which("xdg-open")
        if opener is None:
            raise NotSupported("No way to open things on this system.")
        subprocess.Popen([opener, target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def reveal(self, path: str) -> None:
        self.open(os.path.dirname(path))

    # files ------------------------------------------------------------------------------
    def find_files(self, query: str, kind: str = "", limit: int = 8) -> list[FileHit]:
        tokens = [token for token in query.lower().split() if token]
        extensions = KIND_EXTENSIONS.get(kind, set())
        hits: list[FileHit] = []
        deadline = time.monotonic() + 2.5
        for root_name in SEARCH_ROOTS:
            root = Path(self.home) / root_name
            if not root.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in SKIP_PARTS and not d.startswith(".")]
                if dirpath.count(os.sep) - str(root).count(os.sep) > 5 or time.monotonic() > deadline:
                    dirnames[:] = []
                for filename in filenames:
                    lowered = filename.lower()
                    if filename.startswith(".") or (extensions and Path(lowered).suffix not in extensions):
                        continue
                    if all(token in lowered for token in tokens):
                        path = os.path.join(dirpath, filename)
                        try:
                            hits.append(FileHit(path, os.path.getmtime(path)))
                        except OSError:
                            continue
        hits.sort(key=lambda hit: hit.modified, reverse=True)
        return hits[:limit]

    # system ---------------------------------------------------------------------------------
    def osascript(self, script: str, timeout: float = 10.0) -> str:
        raise NotSupported("That needs macOS.")

    def notify(self, title: str, text: str) -> None:
        sender = shutil.which("notify-send")
        if sender:
            subprocess.Popen([sender, title, text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def shortcuts(self) -> list[str]:
        raise NotSupported("Shortcuts need macOS.")

    def run_shortcut(self, name: str) -> str:
        raise NotSupported("Shortcuts need macOS.")

    def type_text(self, text: str) -> None:
        raise NotSupported("Typing for you needs macOS.")

    def replace_selection(self, text: str) -> None:
        raise NotSupported("Editing text for you needs macOS.")

    def click(self, x: float, y: float, button: str = "left", count: int = 1) -> None:
        raise NotSupported("Clicking needs macOS.")

    def scroll(self, x: float, y: float, dy: int, dx: int = 0) -> None:
        """Scroll ``dy`` lines (positive = scroll down) at a global point."""
        raise NotSupported("Scrolling needs macOS.")

    # scroll fallbacks when the wheel moves nothing; no-ops here
    def hover(self, x: float, y: float) -> None:
        """Move the pointer to a global point, nothing pressed."""
        raise NotSupported("Pointing needs macOS.")

    def mouse_position(self) -> tuple[float, float] | None:
        return None

    def focused_scroll_area(self):
        """Focused element's scroll area (Rect, global points), or None."""
        return None

    def focused_role(self) -> str | None:
        """Focused element's AX role; "" = nothing focused, None = can't tell."""
        return None

    def scroll_bar_step(self, x: float, y: float, direction: str, *, to_end: bool = False, pages: float = 1.0,
                        within: Rect | None = None) -> bool:
        """Move the scroll bar at (x, y) directly. ``within``: only an area smaller than it. False: no bar."""
        return False

    def scroll_to_visible(self, text: str, *, ask: bool = True) -> Reveal:
        """Find ``text`` on the front page (off-screen too); ``ask`` the app to scroll it into view."""
        return Reveal()

    def press(self, keys: str) -> None:
        """A key or combo like "cmd+t", "return", "pagedown"."""
        raise NotSupported("Pressing keys needs macOS.")

    def drag(self, x1: float, y1: float, x2: float, y2: float) -> None:
        raise NotSupported("Dragging needs macOS.")

    def set_field(self, x: float, y: float, value: str) -> bool:
        raise NotSupported("Filling forms needs macOS.")


class MacHost(PortableHost):
    name = "macos"

    def open_app(self, path: str) -> None:
        run(["open", "-a", path], timeout=8)

    def open(self, target: str) -> None:
        run(["open", target], timeout=8)

    def reveal(self, path: str) -> None:
        run(["open", "-R", path], timeout=8)

    def find_files(self, query: str, kind: str = "", limit: int = 8) -> list[FileHit]:
        """Spotlight: filename matches first, then content matches, newest first within each."""
        seen: dict[str, FileHit] = {}
        kind_clause = f' && kMDItemContentTypeTree == "{SPOTLIGHT_KINDS[kind]}"' if kind in SPOTLIGHT_KINDS else ""
        words = [word.replace('"', "") for word in query.split() if word]
        if not words:
            return []
        by_name = ["mdfind", "-onlyin", self.home,
                   " && ".join(f'kMDItemFSName == "*{word}*"cd' for word in words) + kind_clause]
        spotlight_kind = {"pdf": "pdf", "image": "image", "video": "movie", "audio": "music",
                          "document": "document"}.get(kind)
        by_content = ["mdfind", "-onlyin", self.home, "-interpret",
                      " ".join(words) + (f" kind:{spotlight_kind}" if spotlight_kind else "")]
        for argv in (by_name, by_content):
            try:
                done = run(argv, timeout=6)
            except (OSError, subprocess.TimeoutExpired):
                continue
            batch = []
            for line in done.stdout.splitlines():
                if not line or line in seen or "/." in line or any(part in SKIP_PARTS for part in Path(line).parts):
                    continue
                try:
                    batch.append(FileHit(line, os.path.getmtime(line)))
                except OSError:
                    continue
            for hit in sorted(batch, key=lambda item: item.modified, reverse=True):
                seen.setdefault(hit.path, hit)
            if len(seen) >= limit:
                break
        hits = list(seen.values())[:limit]
        return hits or super().find_files(query, kind, limit)

    def osascript(self, script: str, timeout: float = 10.0) -> str:
        done = run(["osascript", "-e", script], timeout=timeout)
        if done.returncode != 0:
            raise RuntimeError(done.stderr.strip() or "AppleScript failed")
        return done.stdout.strip()

    def notify(self, title: str, text: str) -> None:
        try:
            self.osascript(f"display notification {applescript_string(text)} with title {applescript_string(title)}")
        except Exception:
            pass

    def shortcuts(self) -> list[str]:
        done = run(["shortcuts", "list"], timeout=8)
        return [line.strip() for line in done.stdout.splitlines() if line.strip()]

    def run_shortcut(self, name: str) -> str:
        done = run(["shortcuts", "run", name], timeout=60)
        if done.returncode != 0:
            raise RuntimeError(done.stderr.strip() or f"The {name} shortcut failed")
        return done.stdout.strip()

    # keyboard / accessibility -------------------------------------------------------------
    def type_text(self, text: str) -> None:
        """Type at the cursor. Long/multi-line text is pasted; short text goes one key per char, no modifiers
        (browsers drop chars from multi-char events)."""
        if len(text) > PASTE_OVER or "\n" in text:
            self.paste(text)
            return
        import Quartz

        source = _source(Quartz)
        for char in text:
            if char == "\t":
                self._key(48)
            else:
                code, shift = _US_KEYS.get(char, (0, False))
                for down in (True, False):
                    event = Quartz.CGEventCreateKeyboardEvent(source, code, down)
                    Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskShift if shift else 0)
                    Quartz.CGEventKeyboardSetUnicodeString(event, len(char), char)
                    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            time.sleep(0.004)

    def _key(self, keycode: int, flags: int = 0) -> None:
        import Quartz

        source = _source(Quartz)
        for down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(source, keycode, down)
            Quartz.CGEventSetFlags(event, flags)            # ignore modifiers the user holds
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def focused_value(self) -> str | None:
        """Focused field's text, to verify typing (None: can't tell)."""
        try:
            import ApplicationServices as AX

            system = AX.AXUIElementCreateSystemWide()
            error, focused = AX.AXUIElementCopyAttributeValue(system, AX.kAXFocusedUIElementAttribute, None)
            if error != 0 or focused is None:
                return None
            error, value = AX.AXUIElementCopyAttributeValue(focused, AX.kAXValueAttribute, None)
            return str(value) if error == 0 and isinstance(value, str) else None
        except Exception:
            return None

    def focused_frame(self) -> Rect | None:
        """Focused element's frame, global points (None: can't tell)."""
        try:
            import ApplicationServices as AX

            from mcp_vision.buddy.ax_locator import _bounds, _copy

            focused = _copy(AX, AX.AXUIElementCreateSystemWide(), "AXFocusedUIElement")
            return _bounds(AX, focused) if focused is not None else None
        except Exception:
            return None

    def focused_secure(self) -> bool:
        """Password, card or code field? Then its value stays private."""
        try:
            import ApplicationServices as AX

            from mcp_vision.buddy.ax_locator import _copy, _name
            from mcp_vision.buddy.screen_context import looks_secret

            focused = _copy(AX, AX.AXUIElementCreateSystemWide(), "AXFocusedUIElement")
            if focused is None:
                return False
            return _copy(AX, focused, "AXSubrole") == "AXSecureTextField" \
                or looks_secret(_name(AX, focused, "AXTextField"))
        except Exception:
            return True                                  # can't tell: assume secret

    def replace_selection(self, text: str) -> None:
        """Set the focused field's selected text via Accessibility; paste as a fallback."""
        import ApplicationServices as AX

        system = AX.AXUIElementCreateSystemWide()
        error, focused = AX.AXUIElementCopyAttributeValue(system, AX.kAXFocusedUIElementAttribute, None)
        if error == 0 and focused is not None:
            if AX.AXUIElementSetAttributeValue(focused, AX.kAXSelectedTextAttribute, text) == 0:
                return
        self.paste(text)

    def paste(self, text: str) -> None:
        """⌘V ``text``, then restore the old clipboard (all types)."""
        import AppKit
        import Quartz

        board = AppKit.NSPasteboard.generalPasteboard()
        saved = []
        for item in board.pasteboardItems() or []:
            copy = {str(kind): item.dataForType_(kind) for kind in item.types() or []}
            saved.append({kind: data for kind, data in copy.items() if data is not None})
        board.clearContents()
        item = AppKit.NSPasteboardItem.alloc().init()
        item.setString_forType_(text, AppKit.NSPasteboardTypeString)
        item.setString_forType_("", "org.nspasteboard.TransientType")    # clipboard managers skip it
        board.writeObjects_([item])
        ours = board.changeCount()
        self._key(9, Quartz.kCGEventFlagMaskCommand)                  # ⌘V
        time.sleep(0.5)                                                # app reads the clipboard async
        if board.changeCount() != ours:
            return                                                     # copied over since: keep theirs
        board.clearContents()
        if saved:
            restored = []
            for kinds in saved:
                item = AppKit.NSPasteboardItem.alloc().init()
                for kind, data in kinds.items():
                    item.setData_forType_(data, kind)
                restored.append(item)
            board.writeObjects_(restored)

    def click(self, x: float, y: float, button: str = "left", count: int = 1) -> None:
        import Quartz

        clear([(x, y)])                                  # notch island steps aside
        point = Quartz.CGPointMake(x, y)
        down, up, which = {
            "right": (Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp, Quartz.kCGMouseButtonRight),
        }.get(button, (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp, Quartz.kCGMouseButtonLeft))
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved,
                                                                                 point, which))
        time.sleep(0.02)
        for click in range(1, max(1, min(count, 3)) + 1):
            for kind in (down, up):
                event = Quartz.CGEventCreateMouseEvent(None, kind, point, which)
                Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, click)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
                time.sleep(0.03)

    def scroll(self, x: float, y: float, dy: int, dx: int = 0) -> None:
        """Wheel scrolling in small line steps, aimed at (x, y)."""
        import Quartz

        clear([(x, y)])
        point = Quartz.CGPointMake(x, y)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventMouseMoved, point, Quartz.kCGMouseButtonLeft))
        for index in range(max(abs(dy), abs(dx), 1)):
            line_y = (-1 if dy > 0 else 1) if index < abs(dy) else 0      # negative wheel = scroll down
            line_x = (-1 if dx > 0 else 1) if index < abs(dx) else 0
            event = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 2, line_y * 3,
                                                         line_x * 3)
            Quartz.CGEventSetLocation(event, point)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            time.sleep(0.012)

    # wheel fallbacks: Accessibility, replies capped -------------------------------------------
    def _ax(self, timeout: float = 0.2):
        """(AX module, system-wide element) with replies capped at ``timeout``, or (None, None)."""
        try:
            import ApplicationServices as AX
        except ImportError:
            return None, None
        if not AX.AXIsProcessTrusted():
            return None, None
        system = AX.AXUIElementCreateSystemWide()
        try:
            AX.AXUIElementSetMessagingTimeout(system, timeout)     # a hung app can't stall us
        except Exception:
            pass
        return AX, system

    def hover(self, x: float, y: float) -> None:
        """Move the pointer to (x, y) so hover effects show before a scroll."""
        import Quartz

        clear([(x, y)])
        move = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, Quartz.CGPointMake(x, y),
                                              Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, move)

    def mouse_position(self) -> tuple[float, float] | None:
        from mcp_vision.buddy.capture import cursor_position

        return cursor_position()

    def focused_scroll_area(self):
        AX, system = self._ax()
        if AX is None:
            return None
        from mcp_vision.buddy.ax_locator import _bounds, _copy

        area = climb_to(AX, theirs(AX, _copy(AX, system, "AXFocusedUIElement")), "AXScrollArea", depth=40)
        box = _bounds(AX, area) if area is not None else None
        return box if box is not None and box.width > 80 and box.height > 80 else None

    def focused_role(self) -> str | None:
        AX, system = self._ax(0.1)
        return focus_role(AX, system) if AX is not None else None

    def scroll_bar_step(self, x: float, y: float, direction: str, *, to_end: bool = False, pages: float = 1.0,
                        within: Rect | None = None) -> bool:
        """Move the scroll bar directly, for panels that ignore synthetic wheel events."""
        AX, system = self._ax()
        if AX is None:
            return False
        return bar_step(AX, system, x, y, direction, to_end=to_end, pages=pages, within=within)

    def scroll_to_visible(self, text: str, *, ask: bool = True) -> Reveal:
        AX, system = self._ax(0.25)
        if AX is None:
            return Reveal()
        from mcp_vision.buddy.ax_locator import _copy

        app = theirs(AX, _copy(AX, system, "AXFocusedApplication"))
        window = _copy(AX, app, "AXFocusedWindow") if app is not None else None
        return reveal(AX, window, text, ask=ask) if window is not None else Reveal()

    def press(self, keys: str) -> None:
        import Quartz

        code, flags = parse_keys(keys, {
            "cmd": Quartz.kCGEventFlagMaskCommand, "shift": Quartz.kCGEventFlagMaskShift,
            "alt": Quartz.kCGEventFlagMaskAlternate, "ctrl": Quartz.kCGEventFlagMaskControl})
        self._key(code, flags)

    def drag(self, x1: float, y1: float, x2: float, y2: float) -> None:
        import Quartz

        clear([(x1, y1), (x2, y2)])
        left = Quartz.kCGMouseButtonLeft
        start, end = Quartz.CGPointMake(x1, y1), Quartz.CGPointMake(x2, y2)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap,
                           Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, start, left))
        for step in range(1, 21):
            t = step / 20
            point = Quartz.CGPointMake(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap,
                               Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDragged, point, left))
            time.sleep(0.01)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap,
                           Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, end, left))

    def set_field(self, x: float, y: float, value: str) -> bool:
        """Fill the text field at a global point: click it, select all, type the value."""
        import Quartz

        self.click(x, y)
        time.sleep(0.12)
        self._key(0, Quartz.kCGEventFlagMaskCommand)                  # ⌘A inside the field
        time.sleep(0.04)
        self.type_text(value)
        return True


def theirs(AX, element):
    """``element``, or None if it's Plip's own."""
    if element is None:
        return None
    try:
        err, pid = AX.AXUIElementGetPid(element, None)
        if err == 0 and int(pid) == os.getpid():
            return None
    except Exception:
        pass
    return element


def climb_to(AX, element, role: str, depth: int = 16):
    """``element`` or its nearest ancestor with ``role`` (None past ``depth`` levels)."""
    from mcp_vision.buddy.ax_locator import _copy

    for _ in range(depth):
        if element is None or _copy(AX, element, "AXRole") == role:
            return element
        element = _copy(AX, element, "AXParent")
    return None


NOTHING_THERE = {-25212, -25205}      # NoValue, AttributeUnsupported: a real "none"
MAYBE = -25204                        # CannotComplete: timed out, may still happen


def attribute(AX, element, name: str):
    """(value, 0) or (None, AX error); MAYBE if it raised or timed out."""
    try:
        result = AX.AXUIElementCopyAttributeValue(element, name, None)
    except Exception:
        return None, MAYBE
    err, value = result if isinstance(result, tuple) else (0, result)
    return (value, 0) if err == 0 and value is not None else (None, err or -25212)


def focus_role(AX, system) -> str | None:
    """Focused role; "AXTextArea" in a web editor, "" = no focus, None = no answer (send no keys)."""
    focused, err = attribute(AX, system, "AXFocusedUIElement")
    if focused is None:
        return "" if err in NOTHING_THERE else None
    role, _ = attribute(AX, focused, "AXRole")
    editable, err = attribute(AX, focused, "AXEditableAncestor")
    if role is None or err == MAYBE:
        return None
    subrole, _ = attribute(AX, focused, "AXSubrole")
    if subrole in {"AXSearchField", "AXSecureTextField"}:
        return str(subrole)
    if editable is not None:
        return "AXTextArea"                                 # contenteditable
    return str(role)


def bar_step(AX, system, x: float, y: float, direction: str, *, to_end: bool = False, pages: float = 1.0,
             within: Rect | None = None) -> bool:
    """Move the bar of the scroll area at (x, y). ``within``: skip areas nearly that big (the whole page)."""
    from mcp_vision.buddy.ax_locator import _bounds, _copy

    try:
        err, hit = AX.AXUIElementCopyElementAtPosition(system, x, y, None)
    except Exception:
        return False
    area = climb_to(AX, theirs(AX, hit) if err == 0 else None, "AXScrollArea", depth=40)   # deep web pages
    if area is None:
        return False
    if within is not None:
        box = _bounds(AX, area)
        if box is None or box.width * box.height >= 0.8 * within.width * within.height:
            return False
    sideways = direction in {"left", "right"}
    bar = _copy(AX, area, "AXHorizontalScrollBar" if sideways else "AXVerticalScrollBar")
    return bar is not None and step_bar(AX, bar, direction in {"down", "right"}, to_end=to_end,
                                        share=page_share(AX, area, sideways) * pages)


def page_share(AX, area, sideways: bool = False) -> float:
    """One page as a share of the bar's 0-1 range."""
    from mcp_vision.buddy.ax_locator import _bounds, _copy

    contents = list(_copy(AX, area, "AXContents") or [])
    view, content = _bounds(AX, area), _bounds(AX, contents[0]) if contents else None
    if view is None or content is None:
        return 0.1
    shown, total = (view.width, content.width) if sideways else (view.height, content.height)
    if total <= shown:
        return 0.1
    return max(0.02, min(1.0, shown * 0.9 / (total - shown)))


def step_bar(AX, bar, forward: bool, *, to_end: bool = False, share: float = 0.1) -> bool:
    """Step a scroll bar (AXIncrement/AXDecrement, else nudge by ``share``), or jump to the end."""
    from mcp_vision.buddy.ax_locator import _copy

    try:
        if to_end:
            return AX.AXUIElementSetAttributeValue(bar, "AXValue", 1.0 if forward else 0.0) == 0
        if AX.AXUIElementPerformAction(bar, "AXIncrement" if forward else "AXDecrement") == 0:
            return True
        value = _copy(AX, bar, "AXValue")                  # no step action: nudge the value
        if isinstance(value, (int, float)):
            nudged = min(1.0, max(0.0, float(value) + (share if forward else -share)))
            return AX.AXUIElementSetAttributeValue(bar, "AXValue", nudged) == 0
    except Exception:
        pass
    return False


def reveal(AX, window, text: str, *, ask: bool = True) -> Reveal:
    """Find ``text`` in ``window`` (off-screen too); ``ask``: AXScrollToVisible it. Also says where to wheel,
    or where it is if already in view."""
    from mcp_vision.buddy.ax_context import find_text, visible_spot
    from mcp_vision.buddy.ax_locator import _bounds

    element = find_text(AX, window, text)
    if element is None:
        return Reveal()
    spot, view = visible_spot(AX, element, _bounds(AX, window))
    box = _bounds(AX, element)
    direction, at = "", None
    if box is not None and view is not None:
        direction = "down" if box.y >= view.y + view.height else "up" if box.y + box.height <= view.y else ""
        at = box.center if not direction and view.contains(*box.center) else None
    asked = False
    if ask:
        try:
            asked = AX.AXUIElementPerformAction(element, "AXScrollToVisible") in {0, MAYBE}   # slow isn't no
        except Exception:
            asked = False
    return Reveal(found=True, asked=asked, scroller=spot, direction=direction, at=at)


PASTE_OVER = 40               # chars; longer text is pasted


def poll(check, timeout: float, interval: float = 0.15):
    """``check()`` until truthy (returned), or None after ``timeout`` s."""
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def _source(Quartz):
    """Private event source: ignores modifiers the user holds."""
    try:
        return Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
    except Exception:
        return None


# US key codes for apps that read the key; the char rides along too, so other layouts work
_US_KEYS: dict[str, tuple[int, bool]] = {}
for _chars, _shift in (("asdfhgzxcv\x00bqweryt123465=97-80]ou[ip\x00lj'k;\\,/nm.\x00 `", False),
                       ("ASDFHGZXCV\x00BQWERYT!@#$^%+(&_*)}OU{IP\x00LJ\"K:|<?NM>\x00 ~", True)):
    for _code, _char in enumerate(_chars):
        if _char != "\x00" and _char not in _US_KEYS:
            _US_KEYS[_char] = (_code, _shift)


def default_host() -> PortableHost:
    return MacHost() if sys.platform == "darwin" else PortableHost()

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
        shown = self.path.replace(home, "~", 1) if self.path.startswith(home) else self.path
        return {"title": os.path.basename(self.path), "detail": os.path.dirname(shown),
                "path": self.path, "modified": int(self.modified)}


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
        """Type ``text`` where the cursor is.

        Long or multi-line text is pasted: instant, exact, and no app drops or reorders it. Short text is
        typed one character per key event (browsers and web editors lose characters from multi-character
        events), with real Tab keys and no modifier flags, so a held ⌃⌥ can't turn letters into shortcuts.
        """
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
            Quartz.CGEventSetFlags(event, flags)            # exactly these modifiers, none held by the user
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def focused_value(self) -> str | None:
        """The focused text field's contents, to check typing landed (None: can't tell)."""
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
        """Where the focused element is, global points (None: can't tell). Tells a click's field from the last one."""
        try:
            import ApplicationServices as AX

            from mcp_vision.buddy.ax_locator import _bounds, _copy

            focused = _copy(AX, AX.AXUIElementCreateSystemWide(), "AXFocusedUIElement")
            return _bounds(AX, focused) if focused is not None else None
        except Exception:
            return None

    def focused_secure(self) -> bool:
        """A password box, or one labeled like a card number or a code? Then what it reads stays private."""
        try:
            import ApplicationServices as AX

            from mcp_vision.buddy.ax_locator import _copy, _name
            from mcp_vision.buddy.screen_context import SECRET

            focused = _copy(AX, AX.AXUIElementCreateSystemWide(), "AXFocusedUIElement")
            if focused is None:
                return False
            return _copy(AX, focused, "AXSubrole") == "AXSecureTextField" \
                or bool(SECRET.search(_name(AX, focused, "AXTextField")))
        except Exception:
            return True                                  # can't tell: keep it to itself

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
        """Paste ``text`` with ⌘V, then put back whatever was on the clipboard (images and rich text too)."""
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
        item.setString_forType_("", "org.nspasteboard.TransientType")    # clipboard managers: don't keep this
        board.writeObjects_([item])
        self._key(9, Quartz.kCGEventFlagMaskCommand)                  # ⌘V
        time.sleep(0.3)                                                # the app reads the clipboard on its own time
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

    def press(self, keys: str) -> None:
        import Quartz

        code, flags = parse_keys(keys, {
            "cmd": Quartz.kCGEventFlagMaskCommand, "shift": Quartz.kCGEventFlagMaskShift,
            "alt": Quartz.kCGEventFlagMaskAlternate, "ctrl": Quartz.kCGEventFlagMaskControl})
        self._key(code, flags)

    def drag(self, x1: float, y1: float, x2: float, y2: float) -> None:
        import Quartz

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


PASTE_OVER = 40               # characters; longer text is pasted instead of typed


def poll(check, timeout: float, interval: float = 0.15):
    """``check()`` until it returns something truthy (returned), or None after ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def _source(Quartz):
    """A private event source: keys we send don't pick up modifiers the user is holding."""
    try:
        return Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
    except Exception:
        return None


# US virtual key codes, so apps that look at the key (not just the character) see a real one.
# The character itself always travels in the event too, so other layouts still type correctly.
_US_KEYS: dict[str, tuple[int, bool]] = {}
for _chars, _shift in (("asdfhgzxcv\x00bqweryt123465=97-80]ou[ip\x00lj'k;\\,/nm.\x00 `", False),
                       ("ASDFHGZXCV\x00BQWERYT!@#$^%+(&_*)}OU{IP\x00LJ\"K:|<?NM>\x00 ~", True)):
    for _code, _char in enumerate(_chars):
        if _char != "\x00" and _char not in _US_KEYS:
            _US_KEYS[_char] = (_code, _shift)


def default_host() -> PortableHost:
    return MacHost() if sys.platform == "darwin" else PortableHost()

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

    def click(self, x: float, y: float) -> None:
        raise NotSupported("Clicking needs macOS.")

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
        import Quartz

        for start in range(0, len(text), 16):
            chunk = text[start:start + 16]
            for down in (True, False):
                event = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
                Quartz.CGEventKeyboardSetUnicodeString(event, len(chunk), chunk)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            time.sleep(0.012)

    def _key(self, keycode: int, flags: int = 0) -> None:
        import Quartz

        for down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, keycode, down)
            if flags:
                Quartz.CGEventSetFlags(event, flags)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

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
        import AppKit
        import Quartz

        board = AppKit.NSPasteboard.generalPasteboard()
        saved = board.stringForType_(AppKit.NSPasteboardTypeString)
        board.clearContents()
        board.setString_forType_(text, AppKit.NSPasteboardTypeString)
        self._key(9, Quartz.kCGEventFlagMaskCommand)                  # ⌘V
        time.sleep(0.35)
        if saved is not None:
            board.clearContents()
            board.setString_forType_(saved, AppKit.NSPasteboardTypeString)

    def click(self, x: float, y: float) -> None:
        import Quartz

        point = Quartz.CGPointMake(x, y)
        for kind in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
            event = Quartz.CGEventCreateMouseEvent(None, kind, point, Quartz.kCGMouseButtonLeft)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            time.sleep(0.03)

    def set_field(self, x: float, y: float, value: str) -> bool:
        """Fill the text field at a global point: click it, select all, type the value."""
        import Quartz

        self.click(x, y)
        time.sleep(0.12)
        self._key(0, Quartz.kCGEventFlagMaskCommand)                  # ⌘A inside the field
        time.sleep(0.04)
        self.type_text(value)
        return True


def default_host() -> PortableHost:
    return MacHost() if sys.platform == "darwin" else PortableHost()

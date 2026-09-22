"""Deterministic native-desktop actions: open apps and switch tabs/windows.

These are real, low-risk macOS actions that must not be guessed by a language
model. Resolution and intent parsing are pure (and testable off macOS); only
``perform`` touches AppKit/Quartz.
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

_PREFIX = re.compile(r"^\s*(?:(?:please|can you|could you|would you|help me(?: to)?)\s+)*", re.I)


def _normalize(name: str) -> str:
    text = (name or "").strip().strip("“”\"'").lower()
    text = re.sub(r"\s*\bapp\b\s*$", "", text)
    text = re.sub(r"^the\s+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _app(display: str, bundle_id: str) -> "AppTarget":
    return AppTarget(name=display, bundle_id=bundle_id)


@dataclass(frozen=True)
class AppTarget:
    name: str
    bundle_id: str = ""
    path: str = ""


# Static aliases resolve on any platform, so routing stays deterministic in CI.
_ALIASES: dict[str, AppTarget] = {
    "notes": _app("Notes", "com.apple.Notes"),
    "calculator": _app("Calculator", "com.apple.calculator"),
    "calc": _app("Calculator", "com.apple.calculator"),
    "finder": _app("Finder", "com.apple.finder"),
    "safari": _app("Safari", "com.apple.Safari"),
    "calendar": _app("Calendar", "com.apple.iCal"),
    "reminders": _app("Reminders", "com.apple.reminders"),
    "messages": _app("Messages", "com.apple.MobileSMS"),
    "imessage": _app("Messages", "com.apple.MobileSMS"),
    "mail": _app("Mail", "com.apple.mail"),
    "photos": _app("Photos", "com.apple.Photos"),
    "music": _app("Music", "com.apple.Music"),
    "maps": _app("Maps", "com.apple.Maps"),
    "facetime": _app("FaceTime", "com.apple.FaceTime"),
    "terminal": _app("Terminal", "com.apple.Terminal"),
    "activity monitor": _app("Activity Monitor", "com.apple.ActivityMonitor"),
    "system settings": _app("System Settings", "com.apple.systempreferences"),
    "settings": _app("System Settings", "com.apple.systempreferences"),
    "system preferences": _app("System Settings", "com.apple.systempreferences"),
    "app store": _app("App Store", "com.apple.AppStore"),
    "preview": _app("Preview", "com.apple.Preview"),
    "textedit": _app("TextEdit", "com.apple.TextEdit"),
    "text edit": _app("TextEdit", "com.apple.TextEdit"),
    "freeform": _app("Freeform", "com.apple.freeform"),
    "shortcuts": _app("Shortcuts", "com.apple.shortcuts"),
    "voice memos": _app("Voice Memos", "com.apple.VoiceMemos"),
    "weather": _app("Weather", "com.apple.weather"),
    "clock": _app("Clock", "com.apple.clock"),
    "contacts": _app("Contacts", "com.apple.AddressBook"),
    "stickies": _app("Stickies", "com.apple.Stickies"),
    "dictionary": _app("Dictionary", "com.apple.Dictionary"),
    "quicktime": _app("QuickTime Player", "com.apple.QuickTimePlayerX"),
    "quicktime player": _app("QuickTime Player", "com.apple.QuickTimePlayerX"),
    "disk utility": _app("Disk Utility", "com.apple.DiskUtility"),
    "console": _app("Console", "com.apple.Console"),
    "keychain access": _app("Keychain Access", "com.apple.keychainaccess"),
    "podcasts": _app("Podcasts", "com.apple.podcasts"),
    "tv": _app("TV", "com.apple.TV"),
    "news": _app("News", "com.apple.news"),
    "stocks": _app("Stocks", "com.apple.stocks"),
    "home": _app("Home", "com.apple.Home"),
    "books": _app("Books", "com.apple.iBooksX"),
    "font book": _app("Font Book", "com.apple.FontBook"),
    "automator": _app("Automator", "com.apple.Automator"),
    "script editor": _app("Script Editor", "com.apple.ScriptEditor2"),
}


def _default_app_dirs() -> tuple[str, ...]:
    return (
        "/Applications",
        "/System/Applications",
        "/System/Applications/Utilities",
        "/Applications/Utilities",
        str(Path.home() / "Applications"),
    )


@lru_cache(maxsize=4)
def _scan_apps(app_dirs: tuple[str, ...]) -> dict[str, AppTarget]:
    found: dict[str, AppTarget] = {}
    for directory in app_dirs:
        root = Path(directory)
        if not root.is_dir():
            continue
        try:
            entries = sorted(root.glob("*.app"))
        except OSError:
            continue
        for entry in entries:
            found.setdefault(_normalize(entry.stem), AppTarget(name=entry.stem, path=str(entry)))
    return found


def resolve_app(name: str, *, aliases: dict[str, AppTarget] | None = None,
                app_dirs: tuple[str, ...] | None = None) -> AppTarget | None:
    """Resolve a spoken/written name to an installed app. Pure and cacheable."""
    key = _normalize(name)
    if not key:
        return None
    table = _ALIASES if aliases is None else aliases
    if key in table:
        return table[key]
    dirs = _default_app_dirs() if app_dirs is None else tuple(app_dirs)
    found = _scan_apps(dirs)
    if key in found:
        return found[key]
    candidates = [target for candidate, target in found.items()
                  if candidate.startswith(key) or key in candidate]
    if len(candidates) == 1:
        return candidates[0]
    return None


@dataclass(frozen=True)
class NativeIntent:
    action: str
    value: str = ""
    summary: str = ""


_TAB_NEXT = re.compile(
    r"\b(?:next|forward)\s+tab\b|\btab\s+(?:forward|right|to the right)\b|"
    r"\bswitch\s+tabs?\b|\bchange\s+tab\b",
    re.I,
)
_TAB_PREV = re.compile(
    r"\b(?:previous|prev|last|back)\s+tab\b|\btab\s+(?:back|left|to the left)\b",
    re.I,
)
_TAB_NUM = re.compile(r"\b(?:go\s+to\s+|switch\s+to\s+|open\s+)?tab\s+(\d{1,2})\b", re.I)
_WIN_NEXT = re.compile(r"\b(?:next|switch|cycle)\s+windows?\b", re.I)
_WIN_PREV = re.compile(r"\b(?:previous|prev|last)\s+window\b", re.I)
_OPEN = re.compile(
    r"^\s*(?:(?:please|can you|could you|would you)\s+)*"
    r"(?:open|launch|start|bring up|switch to|focus)\s+(.+?)\s*[.!]?$",
    re.I,
)
_UI_WORDS = re.compile(
    r"\b(?:button|menu|tab|dialog|window|panel|file|folder|form|page|link|"
    r"dropdown|checkbox|field|section|sidebar|toolbar|attachment|document|error|"
    r"notification|popup|overlay|banner|toast)\b"
)
_DETERMINER = re.compile(r"^(?:the|this|that|my|our|your|a|an|new|another|some)\b")


def _looks_like_app_name(destination: str) -> bool:
    text = destination.strip().lower()
    if not text or len(text) > 40:
        return False
    if _DETERMINER.match(text):
        return False
    if re.search(r"https?://|www\.|\b\w+\.(?:com|org|net|io|dev|app)\b", text):
        return False
    return not _UI_WORDS.search(text)


def parse_intent(request: str) -> NativeIntent | None:
    """Recognize unambiguous native-desktop requests; never guess."""
    text = _PREFIX.sub("", (request or "").strip().lower().replace("’", "'"))
    match = _TAB_NUM.search(text)
    if match:
        return NativeIntent("switch_to_tab", match.group(1), f"Switch to tab {match.group(1)}")
    if _TAB_NEXT.search(text):
        return NativeIntent("switch_tab", "next", "Switch to the next tab")
    if _TAB_PREV.search(text):
        return NativeIntent("switch_tab", "prev", "Switch to the previous tab")
    if _WIN_NEXT.search(text):
        return NativeIntent("switch_window", "next", "Switch to the next window")
    if _WIN_PREV.search(text):
        return NativeIntent("switch_window", "prev", "Switch to the previous window")
    match = _OPEN.match(text)
    if match:
        destination = match.group(1).strip().strip("“”\"'")
        candidate = re.sub(r"^my\s+", "", destination)
        if _looks_like_app_name(candidate):
            target = resolve_app(candidate)
            if target:
                return NativeIntent("open_app", target.name, f"Open {target.name}")
    return None


# --- macOS execution ------------------------------------------------------

_CMD = 0x100000
_SHIFT = 0x20000
_CTRL = 0x40000
_DIGIT_KEYS = {1: 18, 2: 19, 3: 20, 4: 21, 5: 23, 6: 22, 7: 26, 8: 28, 9: 25}
_BROWSER_BUNDLES = {
    "com.google.Chrome", "com.google.Chrome.canary", "com.apple.Safari",
    "com.microsoft.edgemac", "com.brave.Browser", "com.vivaldi.Vivaldi",
    "company.thebrowser.Browser", "org.mozilla.firefox",
}


def _matches(target: AppTarget, bundle_id: str, name: str) -> bool:
    if target.bundle_id and bundle_id == target.bundle_id:
        return True
    return bool(name) and _normalize(name) == _normalize(target.name)


def _open_command(target: AppTarget) -> bool:
    if target.path:
        command = ["open", "-a", target.path]
    elif target.bundle_id:
        command = ["open", "-b", target.bundle_id]
    else:
        command = ["open", "-a", target.name]
    return subprocess.run(command, capture_output=True, timeout=15).returncode == 0


def _frontmost() -> tuple[str, str, int]:
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
    except Exception:
        return ("", "", 0)
    if not app:
        return ("", "", 0)
    return (str(app.bundleIdentifier() or ""), str(app.localizedName() or ""),
            int(app.processIdentifier()))


# NSApplicationActivateAllWindows | NSApplicationActivateIgnoringOtherApps
_ACTIVATE_OPTIONS = 0x01 | 0x02


def _activate(target: AppTarget) -> bool:
    """Bring an already-launched app to the front; `open` alone does not."""
    try:
        from AppKit import NSBundle, NSRunningApplication
    except Exception:
        return False
    bundle_id = target.bundle_id
    if not bundle_id and target.path:
        bundle = NSBundle.bundleWithPath_(target.path)
        bundle_id = str(bundle.bundleIdentifier() or "") if bundle else ""
    apps = (NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_id)
            if bundle_id else [])
    activated = False
    for app in apps:
        if app.activateWithOptions_(_ACTIVATE_OPTIONS):
            activated = True
    return activated


def _activate_pid(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        from AppKit import NSRunningApplication
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid))
    except Exception:
        return False
    return bool(app and app.activateWithOptions_(_ACTIVATE_OPTIONS))


def open_app(name: str, *, opener: Callable[[AppTarget], bool] | None = None,
             frontmost: Callable[[], tuple[str, str, int]] | None = None,
             activator: Callable[[AppTarget], bool] | None = None,
             timeout: float = 4.0) -> dict:
    target = resolve_app(name)
    if target is None:
        return {"ok": False, "verified": False,
                "message": f"I couldn't find an app named “{name}”. Check the name or open it once manually."}
    launch = opener or _open_command
    front = frontmost or _frontmost
    focus = activator or _activate
    try:
        launched = launch(target)
    except Exception as exc:
        return {"ok": False, "verified": False,
                "message": f"Could not open {target.name}: {type(exc).__name__}."}
    if not launched:
        return {"ok": False, "verified": False, "message": f"Could not open {target.name}."}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        bundle_id, app_name, _pid = front()
        if _matches(target, bundle_id, app_name):
            return {"ok": True, "verified": True, "message": f"Opened {target.name}.",
                    "bundle_id": target.bundle_id}
        try:
            focus(target)
        except Exception:
            pass
        time.sleep(0.15)
    bundle_id, app_name, _pid = front()
    if _matches(target, bundle_id, app_name):
        return {"ok": True, "verified": True, "message": f"Opened {target.name}."}
    return {"ok": True, "verified": False,
            "message": f"{target.name} launched but did not come to the front."}


def _window_title(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        import ApplicationServices as AX
        from mcp_vision.macos_ui import _ax_copy
        app = AX.AXUIElementCreateApplication(int(pid))
        window = _ax_copy(AX, app, "AXFocusedWindow") or _ax_copy(AX, app, "AXMainWindow")
        return str(_ax_copy(AX, window, "AXTitle") or "") if window else ""
    except Exception:
        return ""


def shortcut_for(action: str, value: str, bundle_id: str = "") -> tuple[int, int]:
    if action == "switch_to_tab":
        index = max(1, min(9, int(value or 1)))
        return _DIGIT_KEYS[index], _CMD
    if action == "switch_tab":
        if bundle_id in _BROWSER_BUNDLES:
            return (30, _CMD | _SHIFT) if value != "prev" else (33, _CMD | _SHIFT)
        return (48, _CTRL) if value != "prev" else (48, _CTRL | _SHIFT)
    if action == "switch_window":
        return (50, _CMD) if value != "prev" else (50, _CMD | _SHIFT)
    raise ValueError(f"Unsupported shortcut action: {action}")


def _dispatch(pid: int, keycode: int, flags: int) -> None:
    import Quartz
    down = Quartz.CGEventCreateKeyboardEvent(None, keycode, True)
    up = Quartz.CGEventCreateKeyboardEvent(None, keycode, False)
    for event in (down, up):
        if event is not None:
            Quartz.CGEventSetFlags(event, flags)
    if pid > 0:
        Quartz.CGEventPostToPid(pid, down)
        Quartz.CGEventPostToPid(pid, up)
    else:
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


def switch(action: str, value: str = "", *, pid: int = 0, bundle_id: str = "",
           title_fn: Callable[[int], str] | None = None,
           dispatch_fn: Callable[[int, int, int], None] | None = None,
           activate_fn: Callable[[int], bool] | None = None) -> dict:
    read_title = title_fn or _window_title
    send = dispatch_fn or _dispatch
    focus = activate_fn or _activate_pid
    # Key equivalents are only handled by the active app; bring the captured
    # target forward before sending the shortcut.
    try:
        focus(pid)
    except Exception:
        pass
    time.sleep(0.15)
    before = read_title(pid)
    try:
        keycode, flags = shortcut_for(action, value, bundle_id)
        send(pid, keycode, flags)
    except Exception as exc:
        return {"ok": False, "verified": False, "message": f"Could not send the shortcut: {type(exc).__name__}."}
    time.sleep(0.35)
    after = read_title(pid)
    label = {
        ("switch_tab", "next"): "Next tab",
        ("switch_tab", "prev"): "Previous tab",
        ("switch_window", "next"): "Next window",
        ("switch_window", "prev"): "Previous window",
    }.get((action, value), f"Tab {value}" if action == "switch_to_tab" else "Shortcut")
    if before and after and before != after:
        return {"ok": True, "verified": True, "message": f"{label} · now showing “{after[:60]}”."}
    if before and after:
        return {"ok": False, "verified": False,
                "message": f"{label} was sent, but the visible window did not change. This app may not use that shortcut."}
    return {"ok": True, "verified": False,
            "message": f"{label} was sent to the frontmost app; the switch could not be visually verified."}


def perform(action: str, value: str = "", pid: int = 0, bundle_id: str = "", **options) -> dict:
    """Execute one native intent. Synchronous; call from a worker thread."""
    if sys.platform != "darwin":
        return {"ok": False, "verified": False,
                "message": "Opening apps and switching tabs is only supported on macOS."}
    if action == "open_app":
        return open_app(value, **options)
    if action in {"switch_tab", "switch_window", "switch_to_tab"}:
        return switch(action, value, pid=int(pid or 0), bundle_id=bundle_id or "", **options)
    return {"ok": False, "verified": False, "message": f"Unsupported native action: {action}"}

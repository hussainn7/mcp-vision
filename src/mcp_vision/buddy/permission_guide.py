"""Getting a macOS permission switched on without hunting through System Settings.

The pattern from Codex's computer use: "Allow" opens the exact Privacy & Security
page, and a small Plip card docks to the bottom of the System Settings window. It
has an arrow, "Drag Plip into the list above", and Plip's own icon to drag there:
macOS adds an app that's dropped into the list already switched on, so there's no
system alert to click through. If Plip is already listed, the card says to flip its
switch. The card follows the window, steps aside when System Settings isn't in
front, and checks the permission twice a second: once it's on, the card says so,
closes, and Plip comes back to the front.

This module is the platform-neutral part (which page, where the card sits, what it
does next), so it's tested anywhere; ``guide_macos.py`` draws the card.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

PANES = {"screen": "Privacy_ScreenCapture", "accessibility": "Privacy_Accessibility",
         "microphone": "Privacy_Microphone", "speech": "Privacy_SpeechRecognition", "contacts": "Privacy_Contacts",
         "automation": "Privacy_Automation", "fulldisk": "Privacy_AllFiles"}
NAMES = {"screen": "Screen Recording", "accessibility": "Accessibility", "microphone": "Microphone",
         "speech": "Speech Recognition", "contacts": "Contacts", "automation": "Automation",
         "fulldisk": "Full Disk Access"}
# Lists that take an app dropped into them; the others only list apps that have asked.
DRAGGABLE = {"accessibility", "screen", "fulldisk"}
SETTINGS_APP = "com.apple.systempreferences"

PANEL = (560.0, 150.0)            # the card's window; the card is drawn inside it with room for its shadow
ROW = (36.0, 56.0, 488.0, 44.0)   # the draggable app row: x, y from the panel's top-left, width, height
SIDEBAR = 190.0                   # System Settings' sidebar: the card centers on the content to its right

Frame = tuple[float, float, float, float]       # x, y, width, height (AppKit: y up, from the primary screen)


def bundle_for(executable: str) -> str:
    """``/Applications/X.app/Contents/MacOS/X`` -> ``/Applications/X.app``; "" when it isn't in an app."""
    marker = executable.find(".app/Contents/")
    return executable[: marker + 4] if marker != -1 else ""


def pane_url(permission: str) -> str:
    return f"x-apple.systempreferences:com.apple.preference.security?{PANES.get(permission, 'Privacy')}"


def card_text(permission: str, app: str) -> tuple[str, str]:
    """The card's two lines for this permission."""
    name = NAMES.get(permission, "this")
    if permission in DRAGGABLE:
        return f"Drag {app} into the list above", f"That turns on {name}. Already listed? Just switch it on."
    return f"Switch on {app} in the list above", f"That turns on {name} for {app}."


def settings_window(windows: list[dict], pid: int) -> Frame | None:
    """System Settings' main window, in CoreGraphics coordinates (y down), from CGWindowList entries."""
    best: Frame | None = None
    for info in windows:
        if int(info.get("kCGWindowOwnerPID", -1)) != pid or int(info.get("kCGWindowLayer", 1)) != 0:
            continue
        bounds = info.get("kCGWindowBounds") or {}
        frame = (float(bounds.get("X", 0)), float(bounds.get("Y", 0)), float(bounds.get("Width", 0)),
                 float(bounds.get("Height", 0)))
        if frame[2] > 320 and frame[3] > 240 and (best is None or frame[2] * frame[3] > best[2] * best[3]):
            best = frame
    return best


def appkit_frame(frame: Frame, primary_height: float) -> Frame:
    """CoreGraphics (top-left origin, y down) -> AppKit (bottom-left origin, y up), both global."""
    x, y, width, height = frame
    return x, primary_height - y - height, width, height


def dock(settings: Frame, visible: Frame, size: tuple[float, float] = PANEL) -> tuple[float, float]:
    """Where the card's window goes: centered under the content of the System Settings window, inside its
    bottom edge, kept on screen. All AppKit coordinates."""
    x, y, width, _height = settings
    card_w, card_h = size
    content_x = x + SIDEBAR
    content_w = max(width - SIDEBAR, card_w)
    left = content_x + (content_w - card_w) / 2
    bottom = y - 4                                    # the card's own margin keeps it just inside the edge
    vx, vy, vw, vh = visible
    return (min(max(left, vx + 8), vx + vw - card_w - 8), min(max(bottom, vy + 8), vy + vh - card_h - 8))


@dataclass
class Step:
    """What the card does after one tick."""

    show: tuple[float, float] | None = None       # place it here and show it
    hide: bool = False                            # System Settings isn't in front: step aside
    granted: bool = False                         # the permission just came on: show the check
    close: bool = False                           # done (or given up): close the card for good


class GuideFlow:
    """The card's decisions, one ``tick`` at a time (every 150 ms on the main thread)."""

    def __init__(self, permission: str, *, clock: Callable[[], float] = time.monotonic,
                 timeout: float = 600.0, linger: float = 1.4, gone: float = 5.0):
        self.permission = permission
        self.clock = clock
        self.started = clock()
        self.timeout = timeout
        self.linger = linger
        self.gone = gone                              # System Settings closed this long: they've stopped
        self.granted_at: float | None = None
        self.origin: tuple[float, float] | None = None
        self.last_open = self.started

    @property
    def done(self) -> bool:
        return self.granted_at is not None

    def tick(self, *, granted: bool | None, settings_front: bool, frame: Frame | None,
             visible: Frame | None, settings_open: bool = True) -> Step:
        now = self.clock()
        if self.granted_at is not None:
            return Step(close=True) if now - self.granted_at >= self.linger else Step()
        if granted:
            self.granted_at = now
            return Step(granted=True, show=self.origin)
        if settings_open:
            self.last_open = now
        if now - self.started > self.timeout or now - self.last_open > self.gone:
            return Step(close=True)
        if not settings_front or frame is None or visible is None:
            return Step(hide=True)
        self.origin = dock(frame, visible)
        return Step(show=self.origin)

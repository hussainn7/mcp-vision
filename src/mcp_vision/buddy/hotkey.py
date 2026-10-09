"""Hold Control+Option (or the shortcut picked in Settings) to talk.

``ChordDetector`` is the platform-neutral rule (tested on any OS); the macOS
listener feeds it from a listen-only Quartz event tap, falling back to
NSEvent global monitors when the tap cannot be created.

Like Clicky, it is a modifier-only chord: pressing both Control and Option
starts listening, releasing either stops. If a regular key is pressed while
the chord is held (Control+Option+Arrow in some app), it was a different
shortcut, so the recording is cancelled instead of submitted.

macOS hands keystrokes only to a process with Accessibility (or Input
Monitoring): without it the tap is still made, and then never sees a key.
Run from a terminal, it's the terminal's permission that counts, not Plip's.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass

# Quartz / NSEvent modifier masks (identical values in both APIs).
CONTROL = 1 << 18
OPTION = 1 << 19
SHIFT = 1 << 17
COMMAND = 1 << 20

# In the order macOS writes them: ⌃⌥⇧⌘.
_KEYS = ((CONTROL, "control", "⌃", "Control"), (OPTION, "option", "⌥", "Option"),
         (SHIFT, "shift", "⇧", "Shift"), (COMMAND, "command", "⌘", "Command"))
CHORDS = ("control+option", "option+command", "control+shift", "control+command")   # what Settings offers
DEFAULT_CHORD = CHORDS[0]


@dataclass(frozen=True)
class Chord:
    id: str
    mask: int

    @property
    def keys(self) -> list[str]:
        return [symbol for bit, _, symbol, _ in _KEYS if self.mask & bit]

    @property
    def symbols(self) -> str:
        return "".join(self.keys)

    @property
    def label(self) -> str:
        return " + ".join(word for bit, _, _, word in _KEYS if self.mask & bit)

    def card(self) -> dict:
        return {"id": self.id, "keys": self.keys, "label": self.label}


def chord(name: str | None) -> Chord:
    """One of the shortcuts Settings offers; anything else is Control + Option."""
    name = (name or "").strip().lower()
    if name not in CHORDS:
        name = DEFAULT_CHORD
    parts = set(name.split("+"))
    return Chord(name, sum(bit for bit, key, _, _ in _KEYS if key in parts))


def can_listen() -> bool:
    """Whether macOS passes keystrokes on to this process (Accessibility, or Input Monitoring)."""
    try:
        import ApplicationServices as AX
        import Quartz
    except ImportError:
        return False
    try:
        if AX.AXIsProcessTrusted():
            return True
        return bool(Quartz.CGPreflightListenEventAccess())
    except Exception:
        return False


def keyboard_owner() -> str:
    """The app whose permission macOS checks: Plip when it's the app, else the terminal it was started from."""
    if ".app/Contents/" in sys.prefix + "/":
        return "Plip"
    terminals = {"Apple_Terminal": "Terminal", "iTerm.app": "iTerm", "vscode": "your code editor",
                 "WarpTerminal": "Warp", "ghostty": "Ghostty"}
    return terminals.get(os.environ.get("TERM_PROGRAM", ""), "the app you started Plip from")


class ChordDetector:
    def __init__(self, *, on_press: Callable[[], None], on_release: Callable[[], None],
                 on_cancel: Callable[[], None], chord: int = CONTROL | OPTION):
        self.on_press = on_press
        self.on_release = on_release
        self.on_cancel = on_cancel
        self.chord = chord
        self.held = False
        self._cancelled = False

    def chord_down(self, flags: int) -> bool:
        """All of its modifiers held, and no other Control, Option or Command (that's another app's shortcut).

        An extra Shift is fine: it's held by accident more than it means anything.
        """
        others = (CONTROL | OPTION | COMMAND) & ~self.chord
        return flags & self.chord == self.chord and not flags & others

    def set_chord(self, mask: int) -> None:
        """A new shortcut from Settings. A press of the old one in progress is dropped, never sent."""
        if self.held:
            self.held, self._cancelled = False, False
            self.on_cancel()
        self.chord = mask

    def flags_changed(self, flags: int) -> None:
        down = self.chord_down(flags)
        if down and not self.held:
            self.held = True
            self._cancelled = False
            self.on_press()
        elif not down and self.held and flags & self.chord == self.chord:
            self.key_down()                  # ⌘ joined ⌃⌥ (Rectangle's ⌃⌥⌘→): another app's shortcut, not a release
        elif not down and self.held:
            self.held = False
            if self._cancelled:
                self._cancelled = False
            else:
                self.on_release()

    def key_down(self) -> None:
        if self.held and not self._cancelled:
            self._cancelled = True
            self.on_cancel()


class MacHotkeyListener:
    """Install the chord listener on the main run loop (main thread only)."""

    def __init__(self, detector: ChordDetector, *, allowed: Callable[[], bool] = can_listen):
        self.detector = detector
        self.allowed = allowed
        self.tap = None
        self.monitors: list = []
        self.mechanism = "none"
        self._live = False            # made while macOS was passing keys on

    def start(self) -> str:
        """Returns which mechanism works: 'event-tap', 'nsevent', or 'none'.

        'none' also when the tap was made but macOS won't hand it a key (no Accessibility for whoever
        runs Plip): it used to say 'event-tap' there, so Plip looked ready and ⌃⌥ did nothing.
        """
        self._live = self.allowed()
        self.mechanism = "event-tap" if self._start_tap() else "nsevent" if self._start_monitors() else "none"
        return self.mode()

    def mode(self) -> str:
        """What works right now. Permission granted since start: listen again, a tap made before never hears."""
        if not self.allowed():
            return "none"
        if not self._live:
            self.stop()
            return self.start()
        return self.mechanism

    def stop(self) -> None:
        try:
            import Quartz

            if self.tap is not None:
                Quartz.CGEventTapEnable(self.tap, False)
                Quartz.CFRunLoopRemoveSource(Quartz.CFRunLoopGetMain(), self._source, Quartz.kCFRunLoopCommonModes)
                Quartz.CFMachPortInvalidate(self.tap)
        except Exception:
            pass
        try:
            import AppKit

            for monitor in self.monitors:
                AppKit.NSEvent.removeMonitor_(monitor)
        except Exception:
            pass
        self.tap, self.monitors, self.mechanism = None, [], "none"

    def _start_tap(self) -> bool:
        try:
            import Quartz
        except ImportError:
            return False

        def callback(_proxy, event_type, event, _refcon):
            if event_type in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
                if self.tap is not None:
                    Quartz.CGEventTapEnable(self.tap, True)
                return event
            try:
                if event_type == Quartz.kCGEventFlagsChanged:
                    self.detector.flags_changed(int(Quartz.CGEventGetFlags(event)))
                elif event_type == Quartz.kCGEventKeyDown:
                    self.detector.key_down()
            except Exception:
                import traceback
                traceback.print_exc()
            return event

        mask = (1 << Quartz.kCGEventFlagsChanged) | (1 << Quartz.kCGEventKeyDown)   # CGEventMaskBit
        tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                      Quartz.kCGEventTapOptionListenOnly, mask, callback, None)
        if tap is None:
            return False
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetMain(), source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        self.tap = tap
        self._callback = callback        # keep the Python callable alive
        self._source = source
        return True

    def _start_monitors(self) -> bool:
        try:
            import AppKit
            import ApplicationServices as AX
        except ImportError:
            return False
        if not AX.AXIsProcessTrusted():
            # Global monitors install fine without Accessibility but never fire;
            # report failure so the menu tells the user to grant it.
            return False

        def handle(event):
            try:
                if event.type() == AppKit.NSEventTypeFlagsChanged:
                    self.detector.flags_changed(int(event.modifierFlags()))
                elif event.type() == AppKit.NSEventTypeKeyDown:
                    self.detector.key_down()
            except Exception:
                import traceback
                traceback.print_exc()

        mask = AppKit.NSEventMaskFlagsChanged | AppKit.NSEventMaskKeyDown

        def local(event):
            handle(event)
            return event

        global_monitor = AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(mask, handle)
        local_monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask, local)
        self.monitors = [m for m in (global_monitor, local_monitor) if m is not None]
        return bool(global_monitor)

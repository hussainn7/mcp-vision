"""Hold Control+Option to talk.

``ChordDetector`` is the platform-neutral rule (tested on any OS); the macOS
listener feeds it from a listen-only Quartz event tap, falling back to
NSEvent global monitors when the tap cannot be created.

Like Clicky, it is a modifier-only chord: pressing both Control and Option
starts listening, releasing either stops. If a regular key is pressed while
the chord is held (Control+Option+Arrow in some app), it was a different
shortcut, so the recording is cancelled instead of submitted.
"""
from __future__ import annotations

from collections.abc import Callable

# Quartz / NSEvent modifier masks (identical values in both APIs).
CONTROL = 1 << 18
OPTION = 1 << 19
SHIFT = 1 << 17
COMMAND = 1 << 20


class ChordDetector:
    def __init__(self, *, on_press: Callable[[], None], on_release: Callable[[], None],
                 on_cancel: Callable[[], None]):
        self.on_press = on_press
        self.on_release = on_release
        self.on_cancel = on_cancel
        self.held = False
        self._cancelled = False

    @staticmethod
    def chord_down(flags: int) -> bool:
        return bool(flags & CONTROL) and bool(flags & OPTION) and not (flags & COMMAND)

    def flags_changed(self, flags: int) -> None:
        down = self.chord_down(flags)
        if down and not self.held:
            self.held = True
            self._cancelled = False
            self.on_press()
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

    def __init__(self, detector: ChordDetector):
        self.detector = detector
        self.tap = None
        self.monitors: list = []

    def start(self) -> str:
        """Returns which mechanism is active: 'event-tap', 'nsevent', or 'none'."""
        if self._start_tap():
            return "event-tap"
        if self._start_monitors():
            return "nsevent"
        return "none"

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
        except ImportError:
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

"""PID-targeted macOS keyboard delivery that does not move the user's cursor."""
from __future__ import annotations

from dataclasses import dataclass


class TargetedInputUnavailable(RuntimeError):
    def __init__(self, message: str, *, events_posted: int = 0):
        super().__init__(message)
        self.events_posted = events_posted

    @property
    def delivery_unknown(self) -> bool:
        return self.events_posted > 0


@dataclass(frozen=True)
class InputDispatch:
    events_posted: int
    complete: bool = True


def _event(quartz, keycode: int, down: bool, flags: int = 0, text: str = ""):
    event = quartz.CGEventCreateKeyboardEvent(None, keycode, down)
    if event is None:
        raise TargetedInputUnavailable("CoreGraphics could not create a keyboard event")
    if flags:
        quartz.CGEventSetFlags(event, flags)
    if text:
        quartz.CGEventKeyboardSetUnicodeString(event, len(text), text)
    return event


def targeted_replace_text(pid: int, text: str) -> InputDispatch:
    """Select all and insert Unicode, preserving partial-delivery accounting."""
    if pid <= 0:
        raise TargetedInputUnavailable("invalid target pid")
    try:
        import Quartz
    except ImportError as exc:
        raise TargetedInputUnavailable("Quartz is unavailable") from exc
    posted = 0
    try:
        command = int(Quartz.kCGEventFlagMaskCommand)
        for down in (True, False):
            Quartz.CGEventPostToPid(pid, _event(Quartz, 0, down, command))  # Command-A
            posted += 1
        Quartz.CGEventPostToPid(pid, _event(Quartz, 51, True))  # delete selected text
        posted += 1
        Quartz.CGEventPostToPid(pid, _event(Quartz, 51, False))
        posted += 1
        for offset in range(0, len(text), 20):
            chunk = text[offset:offset + 20]
            Quartz.CGEventPostToPid(pid, _event(Quartz, 0, True, text=chunk))
            posted += 1
            Quartz.CGEventPostToPid(pid, _event(Quartz, 0, False, text=chunk))
            posted += 1
        return InputDispatch(events_posted=posted)
    except Exception as exc:
        raise TargetedInputUnavailable(
            f"PID keyboard delivery unavailable: {type(exc).__name__}", events_posted=posted) from exc


def targeted_key(pid: int, keycode: int, flags: int = 0) -> InputDispatch:
    if pid <= 0:
        raise TargetedInputUnavailable("invalid target pid")
    posted = 0
    try:
        import Quartz
        Quartz.CGEventPostToPid(pid, _event(Quartz, keycode, True, flags))
        posted += 1
        Quartz.CGEventPostToPid(pid, _event(Quartz, keycode, False, flags))
        posted += 1
        return InputDispatch(events_posted=posted)
    except Exception as exc:
        raise TargetedInputUnavailable(
            f"PID key delivery unavailable: {type(exc).__name__}", events_posted=posted) from exc

"""PID-targeted macOS keyboard delivery that does not move the user's cursor."""
from __future__ import annotations

import time


class TargetedInputUnavailable(RuntimeError):
    pass


def _event(quartz, keycode: int, down: bool, flags: int = 0, text: str = ""):
    event = quartz.CGEventCreateKeyboardEvent(None, keycode, down)
    if event is None:
        raise TargetedInputUnavailable("CoreGraphics could not create a keyboard event")
    if flags:
        quartz.CGEventSetFlags(event, flags)
    if text:
        quartz.CGEventKeyboardSetUnicodeString(event, len(text), text)
    return event


def targeted_replace_text(pid: int, text: str) -> bool:
    """Select all and insert Unicode into one process; return only dispatch status."""
    if pid <= 0:
        raise TargetedInputUnavailable("invalid target pid")
    try:
        import Quartz
    except ImportError as exc:
        raise TargetedInputUnavailable("Quartz is unavailable") from exc
    try:
        command = int(Quartz.kCGEventFlagMaskCommand)
        for down in (True, False):
            Quartz.CGEventPostToPid(pid, _event(Quartz, 0, down, command))  # Command-A
        Quartz.CGEventPostToPid(pid, _event(Quartz, 51, True))  # delete selected text
        Quartz.CGEventPostToPid(pid, _event(Quartz, 51, False))
        for offset in range(0, len(text), 20):
            chunk = text[offset:offset + 20]
            Quartz.CGEventPostToPid(pid, _event(Quartz, 0, True, text=chunk))
            Quartz.CGEventPostToPid(pid, _event(Quartz, 0, False, text=chunk))
        time.sleep(0.05)
        return True
    except Exception as exc:
        raise TargetedInputUnavailable(f"PID keyboard delivery unavailable: {type(exc).__name__}") from exc


def targeted_key(pid: int, keycode: int) -> bool:
    if pid <= 0:
        raise TargetedInputUnavailable("invalid target pid")
    try:
        import Quartz
        Quartz.CGEventPostToPid(pid, _event(Quartz, keycode, True))
        Quartz.CGEventPostToPid(pid, _event(Quartz, keycode, False))
        return True
    except Exception as exc:
        raise TargetedInputUnavailable(f"PID key delivery unavailable: {type(exc).__name__}") from exc

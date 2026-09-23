"""Hold a hotkey with real CGEvent timing (space=49, option=58)."""
import sys
import time

import Quartz


def post(key, down):
    event = Quartz.CGEventCreateKeyboardEvent(None, key, down)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def hold_hotkey(hold_seconds=4.0):
    post(58, True)   # option down
    time.sleep(0.15)
    post(49, True)   # space down
    time.sleep(hold_seconds)
    post(49, False)  # space up
    time.sleep(0.2)
    post(58, False)  # option up


if __name__ == "__main__":
    hold_hotkey(float(sys.argv[1]) if len(sys.argv) > 1 else 4.0)

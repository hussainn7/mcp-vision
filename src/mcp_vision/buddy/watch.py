"""Notice when the user has done the step Plip asked for.

During a walkthrough Plip takes a tiny grayscale fingerprint of the cursor
screen every ~0.7 s. When it differs enough from the starting view and then
holds still (menus finished animating, the page finished loading), the step
is considered done and Plip looks again properly.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from PIL import Image

FINGERPRINT_SIZE = (64, 40)


def fingerprint(image: Image.Image) -> bytes:
    return image.convert("L").resize(FINGERPRINT_SIZE, Image.Resampling.BILINEAR).tobytes()


GLANCE_SIZE = (160, 100)
GLANCE_LEVEL = 24             # gray levels a pixel has to move to count
GLANCE_PIXELS = 3             # this many pixels moving is a change (a checkbox ticking moves ~4, JPEG noise 0)


def glance(image: Image.Image, box: tuple[float, float, float, float] | None = None) -> bytes:
    """A small gray thumbnail of ``box`` (pixels in ``image``) for "did anything change?"."""
    if box is not None:
        left, top, right, bottom = (int(round(v)) for v in box)
        if right - left > 8 and bottom - top > 8:
            image = image.crop((left, top, right, bottom))
    return image.convert("L").resize(GLANCE_SIZE, Image.Resampling.BILINEAR).tobytes()


def changed(a: bytes, b: bytes) -> bool:
    """True when enough pixels moved for something real to have happened (not JPEG noise, which moves none)."""
    if not a or not b or len(a) != len(b):
        return True
    return sum(1 for x, y in zip(a, b, strict=True) if abs(x - y) > GLANCE_LEVEL) >= GLANCE_PIXELS


def difference(a: bytes, b: bytes) -> float:
    """Mean absolute pixel difference, 0..255."""
    if not a or not b or len(a) != len(b):
        return 255.0
    return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a)


class ScreenWatcher:
    def __init__(self, grab: Callable[[], bytes], *, interval: float = 0.7, threshold: float = 4.0,
                 settle: float = 1.5, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], object] = asyncio.sleep):
        self.grab = grab
        self.interval = interval
        self.threshold = threshold          # mean gray-level change that counts as "something happened"
        self.settle = settle                # change between consecutive frames that counts as "still"
        self.clock = clock
        self.sleep = sleep

    async def wait_for_change(self, timeout: float) -> bool:
        """True once the screen changed and settled; False on timeout."""
        baseline = await asyncio.to_thread(self.grab)
        deadline = self.clock() + timeout
        previous = baseline
        changed = False
        while self.clock() < deadline:
            await self.sleep(self.interval)
            current = await asyncio.to_thread(self.grab)
            if not changed and difference(baseline, current) >= self.threshold:
                changed = True
            elif changed and difference(previous, current) <= self.settle:
                return True
            previous = current
        return False

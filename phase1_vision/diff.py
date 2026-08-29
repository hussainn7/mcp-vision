"""Screenshot delta: skip unchanged frames, crop the region that moved.

Polling loops (wait for a modal, wait for hydration) otherwise resend a
full 1280px frame every step. A cheap pixel hash tells us "nothing
changed"; a bbox crop is what a VLM should see when something did.
"""

from __future__ import annotations

import hashlib

from PIL import Image, ImageChops, ImageFilter, ImageStat


def frame_hash(img: Image.Image, size: int = 64) -> str:
    """Perceptual-ish identity: tiny grayscale digest. Fast, not cryptographic."""
    tiny = img.convert("L").resize((size, size), Image.BILINEAR)
    return hashlib.md5(tiny.tobytes()).hexdigest()


def changed(a: Image.Image, b: Image.Image, max_mean: float = 4.0) -> bool:
    """True when the two frames differ by more than `max_mean` gray levels."""
    if a.size != b.size:
        b = b.resize(a.size, Image.BILINEAR)
    diff = ImageChops.difference(a.convert("L"), b.convert("L"))
    return ImageStat.Stat(diff).mean[0] > max_mean


def delta_bbox(prev: Image.Image, curr: Image.Image, pad: int = 16, min_mean: float = 8.0):
    """Axis-aligned bbox of pixels that moved, or None if the frame is still.

    Returns (x, y, w, h) in `curr` pixels.
    """
    if prev.size != curr.size:
        prev = prev.resize(curr.size, Image.BILINEAR)
    diff = ImageChops.difference(prev.convert("L"), curr.convert("L"))
    # blur so isolated jpeg-ish noise doesn't become a 1px "change"
    mask = diff.filter(ImageFilter.BoxBlur(1)).point(lambda p: 255 if p > min_mean else 0)
    box = mask.getbbox()
    if box is None:
        return None
    x1, y1, x2, y2 = box
    x1 = max(0, x1 - pad)
    y1 = max(0, y1 - pad)
    x2 = min(curr.width, x2 + pad)
    y2 = min(curr.height, y2 + pad)
    return (x1, y1, x2 - x1, y2 - y1)


def crop_delta(prev: Image.Image | None, curr: Image.Image, pad: int = 16):
    """Return (image_to_send, kind) where kind is 'full' | 'crop' | 'skip'.

    First frame is always full. Unchanged -> skip. Local change -> crop.
    A change covering most of the frame is sent full (crop wouldn't save tokens).
    """
    if prev is None:
        return curr, "full"
    box = delta_bbox(prev, curr, pad=pad)
    if box is None:
        return None, "skip"
    x, y, w, h = box
    if w * h > 0.55 * curr.width * curr.height:
        return curr, "full"
    return curr.crop((x, y, x + w, y + h)), "crop"


class FrameBudget:
    """Keep the last sent frame so a polling loop can skip or crop."""

    def __init__(self):
        self.prev = None
        self.sent = 0
        self.skipped = 0
        self.cropped = 0

    def next(self, img: Image.Image):
        out, kind = crop_delta(self.prev, img)
        self.prev = img
        if kind == "skip":
            self.skipped += 1
        elif kind == "crop":
            self.cropped += 1
            self.sent += 1
        else:
            self.sent += 1
        return out, kind


def demo():
    a = Image.new("RGB", (200, 100), (10, 10, 10))
    b = a.copy()
    assert frame_hash(a) == frame_hash(b)
    assert not changed(a, b)
    assert crop_delta(a, b) == (None, "skip")
    assert crop_delta(None, a)[1] == "full"

    # a white square in the corner is a local change -> crop
    c = a.copy()
    for x in range(10, 40):
        for y in range(10, 30):
            c.putpixel((x, y), (255, 255, 255))
    assert changed(a, c)
    img, kind = crop_delta(a, c, pad=4)
    assert kind == "crop" and img is not None
    assert img.width < 200 and img.height < 100
    box = delta_bbox(a, c, pad=0, min_mean=8)
    assert box is not None
    x, y, w, h = box
    assert x <= 10 and y <= 10 and x + w >= 40 and y + h >= 30

    # whole-frame flash is cheaper as a full send
    d = Image.new("RGB", (200, 100), (200, 200, 200))
    assert crop_delta(a, d)[1] == "full"

    bud = FrameBudget()
    assert bud.next(a)[1] == "full"
    assert bud.next(a.copy())[1] == "skip"
    assert bud.next(c)[1] == "crop"
    assert bud.skipped == 1 and bud.cropped == 1 and bud.sent == 2
    print("ok")


if __name__ == "__main__":
    demo()

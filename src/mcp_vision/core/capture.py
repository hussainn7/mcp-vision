"""Multi-monitor capture. Returns a downscaled JPEG/PNG byte array + size."""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import io
import threading
from dataclasses import dataclass
from typing import Callable

from PIL import Image

from mcp_vision.log import get_logger

log = get_logger("mcp_vision.capture")

Grabber = Callable[[int], Image.Image]

_encode_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="capture")


@dataclass
class Frame:
    image: Image.Image
    png: bytes
    display_id: int
    width: int
    height: int
    scale: float
    monitor: dict[str, int]


# ---------------------------------------------------------------------------
# Frame deduplication cache
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    pixel_hash: str
    frame: Frame


class FrameCache:
    """Per-display last-frame cache keyed on a fast pixel hash.

    Call ``get`` before encoding; if it returns a Frame the screen has not
    changed and the expensive encode + double-decode step can be skipped.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[int, _CacheEntry] = {}

    @staticmethod
    def hash(img: Image.Image) -> str:
        data = img.tobytes()
        return hashlib.md5(data, usedforsecurity=False).hexdigest()

    def get(self, display_id: int, pixel_hash: str) -> Frame | None:
        with self._lock:
            entry = self._entries.get(display_id)
            if entry and entry.pixel_hash == pixel_hash:
                return entry.frame
        return None

    def put(self, display_id: int, pixel_hash: str, frame: Frame) -> None:
        with self._lock:
            self._entries[display_id] = _CacheEntry(pixel_hash=pixel_hash, frame=frame)

    def invalidate(self, display_id: int | None = None) -> None:
        with self._lock:
            if display_id is None:
                self._entries.clear()
            else:
                self._entries.pop(display_id, None)


_default_cache: FrameCache = FrameCache()


# ---------------------------------------------------------------------------
# MSS access
# ---------------------------------------------------------------------------

def _monitors() -> list[dict[str, int]]:
    import mss
    with mss.MSS() as sct:
        return [dict(m) for m in sct.monitors]


def list_displays() -> list[dict[str, int]]:
    """mss index 0 is the virtual desktop; 1..n are physical screens."""
    try:
        mons = _monitors()
    except Exception as e:
        log.warning("mss unavailable: %s", e)
        return [{"left": 0, "top": 0, "width": 1280, "height": 800}]
    return mons[1:] if len(mons) > 1 else mons


def _grab_mss(display_id: int) -> Image.Image:
    import mss
    with mss.MSS() as sct:
        mons = sct.monitors
        idx = display_id + 1
        if idx >= len(mons):
            idx = 1 if len(mons) > 1 else 0
        raw = sct.grab(mons[idx])
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


# ---------------------------------------------------------------------------
# Encode helpers
# ---------------------------------------------------------------------------

def _encode_with_image(
    img: Image.Image, max_width: int = 1280, quality: int = 70, fmt: str = "JPEG"
) -> tuple[bytes, Image.Image]:
    """Encode and return (bytes, scaled_image) without reopening bytes."""
    out = img.convert("RGB")
    if out.width > max_width:
        h = int(out.height * max_width / out.width)
        out = out.resize((max_width, h), Image.LANCZOS)
    buf = io.BytesIO()
    if fmt.upper() == "PNG":
        out.save(buf, format="PNG", optimize=True)
    else:
        out.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue(), out


def encode(img: Image.Image, max_width: int = 1280, quality: int = 70, fmt: str = "JPEG") -> bytes:
    """Downscale and compress. JPEG by default — much smaller than PNG for MCP payloads."""
    data, _ = _encode_with_image(img, max_width=max_width, quality=quality, fmt=fmt)
    return data


async def encode_async(
    img: Image.Image, max_width: int = 1280, quality: int = 70, fmt: str = "JPEG"
) -> bytes:
    """Non-blocking encode: runs PIL work in the shared thread pool."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _encode_executor,
        lambda: encode(img, max_width=max_width, quality=quality, fmt=fmt),
    )


# ---------------------------------------------------------------------------
# Main capture entry point
# ---------------------------------------------------------------------------

def capture_display(
    display_id: int = 0,
    max_width: int = 1280,
    grabber: Grabber | None = None,
    cache: FrameCache | None = None,
) -> Frame:
    """Grab one display. Returns a cached Frame if the screen is unchanged."""
    fn = grabber or _grab_mss
    img = fn(display_id)

    fc = cache if cache is not None else _default_cache
    ph = FrameCache.hash(img)
    cached = fc.get(display_id, ph)
    if cached is not None:
        log.debug("capture_display: cache hit display=%d", display_id)
        return cached

    png, small = _encode_with_image(img, max_width=max_width)
    scale = img.width / max(1, small.width)

    mons = list_displays()
    mon = mons[display_id] if display_id < len(mons) else (mons[0] if mons else {
        "left": 0, "top": 0, "width": img.width, "height": img.height,
    })

    frame = Frame(
        image=small,
        png=png,
        display_id=display_id,
        width=small.width,
        height=small.height,
        scale=scale,
        monitor=mon,
    )
    fc.put(display_id, ph, frame)
    return frame


async def capture_display_async(
    display_id: int = 0,
    max_width: int = 1280,
    grabber: Grabber | None = None,
    cache: FrameCache | None = None,
) -> Frame:
    """Async wrapper: grab runs in thread pool to avoid blocking the event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _encode_executor,
        lambda: capture_display(display_id, max_width, grabber, cache),
    )

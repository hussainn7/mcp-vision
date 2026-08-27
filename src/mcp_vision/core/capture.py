"""Multi-monitor capture. Returns a downscaled JPEG/PNG byte array + size."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Callable

from PIL import Image

from mcp_vision.log import get_logger

log = get_logger("mcp_vision.capture")

Grabber = Callable[[int], Image.Image]


@dataclass
class Frame:
    image: Image.Image
    png: bytes
    display_id: int
    width: int
    height: int
    scale: float
    monitor: dict[str, int]


def _monitors() -> list[dict[str, int]]:
    import mss
    with mss.mss() as sct:
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
    with mss.mss() as sct:
        mons = sct.monitors
        # display_id is 0-based over physical screens; mss[0] is all-in-one
        idx = display_id + 1
        if idx >= len(mons):
            idx = 1 if len(mons) > 1 else 0
        raw = sct.grab(mons[idx])
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def encode(img: Image.Image, max_width: int = 1280, quality: int = 70, fmt: str = "JPEG") -> bytes:
    """Downscale and compress. JPEG by default — much smaller than PNG for MCP payloads."""
    out = img.convert("RGB")
    if out.width > max_width:
        h = int(out.height * max_width / out.width)
        out = out.resize((max_width, h), Image.LANCZOS)
    buf = io.BytesIO()
    if fmt.upper() == "PNG":
        out.save(buf, format="PNG", optimize=True)
    else:
        out.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def capture_display(
    display_id: int = 0,
    max_width: int = 1280,
    grabber: Grabber | None = None,
) -> Frame:
    """Grab one display. `grabber` is for tests (no physical screen)."""
    fn = grabber or _grab_mss
    img = fn(display_id)
    png = encode(img, max_width=max_width)
    scale = img.width / max(1, Image.open(io.BytesIO(png)).size[0])
    small = Image.open(io.BytesIO(png)).convert("RGB")
    mons = list_displays()
    mon = mons[display_id] if display_id < len(mons) else (mons[0] if mons else {
        "left": 0, "top": 0, "width": img.width, "height": img.height,
    })
    return Frame(
        image=small,
        png=png,
        display_id=display_id,
        width=small.width,
        height=small.height,
        scale=scale,
        monitor=mon,
    )

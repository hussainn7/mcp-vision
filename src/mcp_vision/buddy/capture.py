"""Capture every display as a model-ready JPEG, cursor screen first."""
from __future__ import annotations

import io
import sys
from collections.abc import Callable
from typing import Any

from PIL import Image

from mcp_vision.buddy.geometry import Rect, ScreenInfo, Screenshot, fit_within, order_cursor_first

Grabber = Callable[[dict[str, int]], Image.Image]


def cursor_position() -> tuple[float, float] | None:
    """Pointer location in global top-left points, or ``None`` when unknown."""
    if sys.platform == "darwin":
        try:
            import Quartz

            point = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
            return float(point.x), float(point.y)
        except Exception:
            return None
    try:
        import pyautogui

        x, y = pyautogui.position()
        return float(x), float(y)
    except Exception:
        return None


def _mac_scale_factors() -> dict[tuple[int, int], float]:
    """Backing scale per display, keyed by its global top-left origin."""
    try:
        from AppKit import NSScreen

        screens = NSScreen.screens()
        primary_height = float(screens[0].frame().size.height)
        factors = {}
        for screen in screens:
            frame = screen.frame()
            top = primary_height - float(frame.origin.y) - float(frame.size.height)
            factors[(int(frame.origin.x), int(top))] = float(screen.backingScaleFactor())
        return factors
    except Exception:
        return {}


def _mss_grab(monitor: dict[str, int]) -> Image.Image:
    import mss

    with mss.MSS() as sct:
        shot = sct.grab(monitor)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def _mss_monitors() -> list[dict[str, int]]:
    import mss

    with mss.MSS() as sct:
        return [dict(monitor) for monitor in sct.monitors[1:]]


class ScreenCapturer:
    """Enumerate displays and encode them for the vision model.

    ``max_edge`` bounds the longest side of each uploaded image. The model's
    point coordinates are in that downscaled pixel space; ``Screenshot``
    carries the dimensions needed to map them back to real screen points.
    """

    def __init__(self, *, max_edge: int = 1280, quality: int = 70,
                 monitors: Callable[[], list[dict[str, int]]] | None = None,
                 grabber: Grabber | None = None,
                 cursor: Callable[[], tuple[float, float] | None] | None = None,
                 scale_factors: Callable[[], dict[tuple[int, int], float]] | None = None):
        self.max_edge = max_edge
        self.quality = quality
        self._monitors = monitors or _mss_monitors
        self._grab = grabber or _mss_grab
        self._cursor = cursor or cursor_position
        self._scales = scale_factors or (_mac_scale_factors if sys.platform == "darwin" else dict)

    def screens(self) -> list[ScreenInfo]:
        monitors = self._monitors()
        pointer = self._cursor()
        scales = self._scales()
        screens = []
        for index, monitor in enumerate(monitors, start=1):
            frame = Rect(float(monitor["left"]), float(monitor["top"]),
                         float(monitor["width"]), float(monitor["height"]))
            screens.append(ScreenInfo(
                index=index, frame=frame,
                scale=scales.get((int(frame.x), int(frame.y)), 1.0),
                is_cursor_screen=bool(pointer and frame.contains(*pointer)),
            ))
        if screens and not any(screen.is_cursor_screen for screen in screens):
            first = screens[0]
            screens[0] = ScreenInfo(index=first.index, frame=first.frame, scale=first.scale,
                                    is_cursor_screen=True, name=first.name)
        return screens

    def capture(self, *, only_cursor_screen: bool = False) -> list[Screenshot]:
        screens = order_cursor_first(self.screens())
        if only_cursor_screen:
            screens = screens[:1]
        return [self.encode(screen, self._grab(self._monitor_for(screen))) for screen in screens]

    def encode(self, screen: ScreenInfo, image: Image.Image) -> Screenshot:
        width, height = fit_within(image.width, image.height, self.max_edge)
        if (width, height) != image.size:
            image = image.resize((width, height), Image.Resampling.LANCZOS)
        if image.mode != "RGB":
            image = image.convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=self.quality, optimize=True)
        return Screenshot(screen=screen, data=buffer.getvalue(), width=width, height=height)

    @staticmethod
    def _monitor_for(screen: ScreenInfo) -> dict[str, Any]:
        frame = screen.frame
        return {"left": int(frame.x), "top": int(frame.y), "width": int(frame.width), "height": int(frame.height)}

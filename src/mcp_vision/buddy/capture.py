"""Capture every display as a model-ready JPEG, cursor screen first."""
from __future__ import annotations

import io
import sys
import threading
from collections.abc import Callable
from typing import Any

from PIL import Image

from mcp_vision.buddy.geometry import Rect, ScreenInfo, Screenshot, fit_within, order_cursor_first

Grabber = Callable[[dict[str, int]], Image.Image]


_POINTER_LOCK = threading.Lock()


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

        # pyautogui shares one Xlib display, and python-xlib isn't thread safe: two
        # threads asking at once can swallow each other's reply and wait forever.
        with _POINTER_LOCK:
            x, y = pyautogui.position()
        return float(x), float(y)
    except (Exception, SystemExit):         # pyautogui exits the process when tkinter is missing
        return None


def _mac_scale_factors() -> dict[tuple[int, int], float]:
    """Backing scale per display, keyed by its global top-left origin.

    Uses CoreGraphics display modes, which are safe off the main thread
    (capture runs on worker threads; NSScreen is AppKit and is not).
    """
    try:
        import Quartz

        err, displays, count = Quartz.CGGetActiveDisplayList(16, None, None)
        if err != 0:
            return {}
        factors = {}
        for display in displays[:count]:
            bounds = Quartz.CGDisplayBounds(display)
            mode = Quartz.CGDisplayCopyDisplayMode(display)
            points = Quartz.CGDisplayModeGetWidth(mode) if mode else 0
            pixels = Quartz.CGDisplayModeGetPixelWidth(mode) if mode else 0
            scale = float(pixels) / float(points) if points else 1.0
            factors[(int(bounds.origin.x), int(bounds.origin.y))] = scale
        return factors
    except Exception:
        return {}


def _mss_grab(monitor: dict[str, int]) -> Image.Image:
    import mss

    with mss.mss() as sct:
        shot = sct.grab(monitor)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def _mss_monitors() -> list[dict[str, int]]:
    import mss

    with mss.mss() as sct:
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

    def glance(self) -> Image.Image:
        """The cursor screen as it is right now, unencoded: did a step change it where the map can't see?"""
        screen = order_cursor_first(self.screens())[0]
        return self._grab(self._monitor_for(screen))

    def fingerprint(self) -> bytes:
        """A tiny grayscale thumbnail of the cursor screen for change detection."""
        from mcp_vision.buddy.watch import fingerprint

        screen = order_cursor_first(self.screens())[0]
        return fingerprint(self._grab(self._monitor_for(screen)))

    def fingerprint_at(self, x: float, y: float, size: float = 300.0) -> bytes | None:
        """The same tiny thumbnail of just the ``size``-point square around a global point (~16 ms): did a
        scroll move anything there, even when the Accessibility map can't tell. None off every screen."""
        from mcp_vision.buddy.watch import fingerprint

        for monitor in self._monitors():
            left, top, width, height = (float(monitor[key]) for key in ("left", "top", "width", "height"))
            if not Rect(left, top, width, height).contains(x, y):
                continue
            side_x, side_y = min(size, width), min(size, height)
            box = {"left": int(max(left, min(x - side_x / 2, left + width - side_x))),
                   "top": int(max(top, min(y - side_y / 2, top + height - side_y))),
                   "width": int(side_x), "height": int(side_y)}
            return fingerprint(self._grab(box))
        return None

    @classmethod
    def from_images(cls, paths: list[str], **options) -> ScreenCapturer:
        """Treat image files as displays laid out left to right (headless testing)."""
        images = [Image.open(path).convert("RGB") for path in paths]
        monitors, left = [], 0
        for image in images:
            monitors.append({"left": left, "top": 0, "width": image.width, "height": image.height})
            left += image.width
        by_left = {monitor["left"]: image for monitor, image in zip(monitors, images, strict=True)}
        return cls(monitors=lambda: monitors, grabber=lambda monitor: by_left[monitor["left"]],
                   cursor=lambda: (1.0, 1.0), scale_factors=dict, **options)

    @staticmethod
    def _monitor_for(screen: ScreenInfo) -> dict[str, Any]:
        frame = screen.frame
        return {"left": int(frame.x), "top": int(frame.y), "width": int(frame.width), "height": int(frame.height)}

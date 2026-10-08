"""Screen geometry shared by capture, pointing, and the overlay.

Three coordinate spaces meet here:

* **image pixels** - the screenshot the model saw, origin top-left of that
  image, already downscaled for upload.
* **global points (top-left)** - Quartz / Accessibility space: origin at the
  top-left of the primary display, y grows downward, logical points.
* **AppKit points (bottom-left)** - NSWindow space: origin at the bottom-left
  of the primary display, y grows upward.

Everything here is pure arithmetic so it can be tested without a display.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height

    def intersect(self, other: Rect) -> Rect | None:
        """The overlap of two rects, or ``None`` when they don't overlap."""
        left, top = max(self.x, other.x), max(self.y, other.y)
        right = min(self.x + self.width, other.x + other.width)
        bottom = min(self.y + self.height, other.y + other.height)
        return Rect(left, top, right - left, bottom - top) if right > left and bottom > top else None

    def clamp(self, x: float, y: float, inset: float = 1.0) -> tuple[float, float]:
        """Keep a point inside the rect so the buddy never flies off-screen."""
        return (min(max(x, self.x + inset), self.x + self.width - inset),
                min(max(y, self.y + inset), self.y + self.height - inset))


@dataclass(frozen=True)
class ScreenInfo:
    """One physical display, described in global top-left points."""

    index: int                 # 1-based, as the model sees it ("screen1")
    frame: Rect                # global top-left points
    scale: float = 1.0         # backing scale factor (2.0 on Retina)
    is_cursor_screen: bool = False
    name: str = ""

    @property
    def label(self) -> str:
        return f"screen{self.index}"


@dataclass(frozen=True)
class Screenshot:
    """An encoded image of one screen, exactly as it is sent to the model."""

    screen: ScreenInfo
    data: bytes
    width: int                 # pixels of the encoded image
    height: int
    media_type: str = "image/jpeg"

    def to_global(self, px: float, py: float) -> tuple[float, float]:
        """Map image pixels the model reported to global top-left points."""
        frame = self.screen.frame
        sx = frame.width / self.width if self.width else 1.0
        sy = frame.height / self.height if self.height else 1.0
        return frame.clamp(frame.x + px * sx, frame.y + py * sy)

    def from_global(self, gx: float, gy: float) -> tuple[float, float]:
        """Inverse of :meth:`to_global` (used by tests and element snapping)."""
        frame = self.screen.frame
        return ((gx - frame.x) * self.width / frame.width,
                (gy - frame.y) * self.height / frame.height)


def fit_within(width: int, height: int, max_edge: int) -> tuple[int, int]:
    """Downscale (never upscale) so the longest edge is at most ``max_edge``."""
    longest = max(width, height)
    if longest <= max_edge or longest == 0:
        return width, height
    ratio = max_edge / longest
    return max(1, round(width * ratio)), max(1, round(height * ratio))


def to_appkit(gx: float, gy: float, primary_height: float) -> tuple[float, float]:
    """Global top-left points -> AppKit bottom-left points."""
    return gx, primary_height - gy


def from_appkit(ax: float, ay: float, primary_height: float) -> tuple[float, float]:
    """AppKit bottom-left points -> global top-left points."""
    return ax, primary_height - ay


def screen_at(screens: list[ScreenInfo], gx: float, gy: float) -> ScreenInfo | None:
    return next((screen for screen in screens if screen.frame.contains(gx, gy)), None)


def order_cursor_first(screens: list[ScreenInfo]) -> list[ScreenInfo]:
    """The screen under the cursor is what the user is talking about; send it first."""
    return sorted(screens, key=lambda screen: (not screen.is_cursor_screen, screen.index))

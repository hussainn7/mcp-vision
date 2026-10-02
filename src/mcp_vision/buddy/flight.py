"""Pure flight-path math for the buddy cursor.

The buddy leaves the user's pointer, arcs over to the target along a
quadratic Bezier curve, swells slightly mid-flight, and rotates so its tip
follows the direction of travel. Coordinates are global top-left points
(y grows downward), so "up" is negative y.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def ease_in_out_cubic(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def flight_duration(distance: float) -> float:
    """Short hops feel snappy; long flights across displays stay readable."""
    return min(max(0.35 + distance / 1800.0, 0.45), 1.15)


@dataclass(frozen=True)
class FlightFrame:
    x: float
    y: float
    angle: float      # degrees, 0 = pointing right, clockwise positive in top-left space
    scale: float


@dataclass(frozen=True)
class FlightPlan:
    start: tuple[float, float]
    control: tuple[float, float]
    end: tuple[float, float]
    duration: float

    @classmethod
    def between(cls, start: tuple[float, float], end: tuple[float, float],
                *, arc: float | None = None) -> FlightPlan:
        sx, sy = start
        ex, ey = end
        dx, dy = ex - sx, ey - sy
        distance = math.hypot(dx, dy)
        height = min(distance * 0.28, 170.0) if arc is None else arc
        mx, my = (sx + ex) / 2, (sy + ey) / 2
        if distance < 1e-6:
            control = (mx, my - height)
        else:
            # Perpendicular to the path; choose the side that bows upward.
            nx, ny = -dy / distance, dx / distance
            if ny > 0:
                nx, ny = -nx, -ny
            control = (mx + nx * height, my + ny * height)
        return cls(start=start, control=control, end=end, duration=flight_duration(distance))

    def point(self, t: float) -> tuple[float, float]:
        u = 1 - t
        (sx, sy), (cx, cy), (ex, ey) = self.start, self.control, self.end
        return (u * u * sx + 2 * u * t * cx + t * t * ex,
                u * u * sy + 2 * u * t * cy + t * t * ey)

    def tangent(self, t: float) -> tuple[float, float]:
        (sx, sy), (cx, cy), (ex, ey) = self.start, self.control, self.end
        return (2 * (1 - t) * (cx - sx) + 2 * t * (ex - cx),
                2 * (1 - t) * (cy - sy) + 2 * t * (ey - cy))

    def frame_at(self, elapsed: float) -> FlightFrame:
        progress = 1.0 if self.duration <= 0 else min(max(elapsed / self.duration, 0.0), 1.0)
        t = ease_in_out_cubic(progress)
        x, y = self.point(t)
        tx, ty = self.tangent(t)
        angle = math.degrees(math.atan2(ty, tx)) if (tx or ty) else 0.0
        scale = 1.0 + 0.35 * math.sin(math.pi * t)
        return FlightFrame(x=x, y=y, angle=angle, scale=scale)

    def frames(self, fps: float = 60.0) -> list[FlightFrame]:
        steps = max(2, int(math.ceil(self.duration * fps)))
        return [self.frame_at(self.duration * i / steps) for i in range(steps + 1)]

"""Pure flight-path math for the buddy cursor.

Tuned to match Clicky's feel: a quadratic Bezier whose control point sits
above the midpoint (the arc bulges upward), smoothstep easing, a 1.3x swell
mid-flight, and a triangle that turns to face the direction of travel.
Coordinates are global top-left points (y grows downward).

Angles describe an upward-pointing triangle rotated clockwise in degrees:
0 points up, 90 points right; -35 is the resting "mouse cursor" pose.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

REST_ANGLE = -35.0


def smoothstep(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return p * p * (3 - 2 * p)


def flight_duration(distance: float) -> float:
    return min(max(distance / 800.0, 0.6), 1.4)


@dataclass(frozen=True)
class FlightFrame:
    x: float
    y: float
    angle: float
    scale: float
    done: bool = False


@dataclass(frozen=True)
class FlightPlan:
    start: tuple[float, float]
    control: tuple[float, float]
    end: tuple[float, float]
    duration: float

    @classmethod
    def between(cls, start: tuple[float, float], end: tuple[float, float]) -> FlightPlan:
        (sx, sy), (ex, ey) = start, end
        distance = math.hypot(ex - sx, ey - sy)
        control = ((sx + ex) / 2, (sy + ey) / 2 - min(distance * 0.2, 80.0))
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
        t = smoothstep(progress)
        x, y = self.point(t)
        tx, ty = self.tangent(t)
        angle = math.degrees(math.atan2(ty, tx)) + 90 if (tx or ty) else REST_ANGLE
        scale = 1.0 + 0.3 * math.sin(math.pi * progress)
        return FlightFrame(x=x, y=y, angle=angle, scale=scale, done=progress >= 1.0)

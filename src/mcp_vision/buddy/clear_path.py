"""Plip's own windows (the notch island) step aside for points it's about to click or point at.

Points are global top-left; a failing listener never blocks the click.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable

Point = tuple[float, float]
Listener = Callable[[list[Point], bool], None]

log = logging.getLogger(__name__)
_listeners: list[Listener] = []


def on_clear(listener: Listener) -> Callable[[], None]:
    """Call ``listener(points, act)`` before Plip touches points; returns an unsubscribe."""
    _listeners.append(listener)

    def stop() -> None:
        if listener in _listeners:
            _listeners.remove(listener)
    return stop


def clear(points: Iterable[Point], *, act: bool = True) -> None:
    """About to act at (``act``) or just point at these points."""
    where = [(float(x), float(y)) for x, y in points]
    for listener in list(_listeners):
        try:
            listener(where, act)
        except Exception:
            log.exception("a window couldn't make way for Plip")

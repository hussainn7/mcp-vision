"""Plip's own windows step aside for what it points at and clicks.

The notch island floats above every app. When Plip clicks, scrolls or drags somewhere it covers (a
browser tab, the address bar, a menu right under the notch), the island must let that click through
to the app.

Hosts call ``clear`` right before they move the real mouse (``act=True``), pointers right before
they fly somewhere (``act=False``). The island listens. Points are global top-left points, the
coordinates hosts click at. A listener that fails never stops the click.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable

Point = tuple[float, float]
Listener = Callable[[list[Point], bool], None]

log = logging.getLogger(__name__)
_listeners: list[Listener] = []


def on_clear(listener: Listener) -> Callable[[], None]:
    """Call ``listener(points, act)`` before Plip touches those points. Returns a function that stops it."""
    _listeners.append(listener)

    def stop() -> None:
        if listener in _listeners:
            _listeners.remove(listener)
    return stop


def clear(points: Iterable[Point], *, act: bool = True) -> None:
    """Plip is about to click / scroll / drag at (``act``), or point at, these points."""
    where = [(float(x), float(y)) for x, y in points]
    for listener in list(_listeners):
        try:
            listener(where, act)
        except Exception:
            log.exception("a window couldn't make way for Plip")

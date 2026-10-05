"""Speed: after an action, Plip looks again as soon as the screen settles, not after a fixed wait."""
from __future__ import annotations

import asyncio
import time

from buddy_fakes import Capturer, ScriptedBrain
from mcp_vision.buddy.companion import Companion
from mcp_vision.buddy.screen_context import Control, ScreenContext


class Loading:
    """A page that changes for a few reads, then stays put (or never settles)."""

    def __init__(self, changes: int):
        self.changes, self.reads = changes, 0

    def snapshot(self) -> ScreenContext:
        self.reads += 1
        step = min(self.reads, self.changes)
        return ScreenContext(app="Safari", controls=[Control(f"result {step}", "AXLink", 100, 40 * step)])


def plip(context, **kw):
    return Companion(brain=ScriptedBrain(), capturer=Capturer(), context=context, settle_interval=0.01, **kw)


def test_it_looks_as_soon_as_the_screen_map_stops_changing():
    page = Loading(changes=4)
    started = time.perf_counter()
    assert asyncio.run(plip(page)._settle(5.0)) is True
    assert time.perf_counter() - started < 1.0 and page.reads == 6        # 4 changes, then 2 quiet reads


def test_a_screen_that_keeps_changing_waits_out_the_limit():
    page = Loading(changes=10_000)
    started = time.perf_counter()
    assert asyncio.run(plip(page)._settle(0.3)) is False
    assert 0.3 <= time.perf_counter() - started < 1.0


def test_without_a_screen_map_it_only_waits_a_moment():
    started = time.perf_counter()
    assert asyncio.run(plip(None)._settle(0.2)) is False
    assert time.perf_counter() - started < 0.5


def test_the_signature_changes_with_what_is_on_screen():
    one = ScreenContext(app="Safari", controls=[Control("Buy", "AXButton", 10, 10)])
    assert one.signature() == ScreenContext(app="Safari", controls=[Control("Buy", "AXButton", 10.2, 9.9)]).signature()
    assert one.signature() != ScreenContext(app="Safari", controls=[Control("Buy", "AXButton", 10, 80)]).signature()
    assert one.signature() != ScreenContext(app="Notes", controls=[Control("Buy", "AXButton", 10, 10)]).signature()

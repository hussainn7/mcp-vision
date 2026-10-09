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
    assert asyncio.run(plip(page)._settle(5.0)) == page.snapshot().signature()
    assert time.perf_counter() - started < 1.0 and page.reads == 7        # 4 changes, 2 quiet reads (+1 here)


def test_a_screen_that_keeps_changing_waits_out_the_limit():
    page = Loading(changes=10_000)
    started = time.perf_counter()
    assert asyncio.run(plip(page)._settle(0.3)) is not None             # what it ended on, never settled
    assert 0.3 <= time.perf_counter() - started < 1.0


def test_without_a_screen_map_it_only_waits_a_moment():
    started = time.perf_counter()
    assert asyncio.run(plip(None)._settle(0.2)) is None
    assert time.perf_counter() - started < 0.5


def test_the_signature_changes_with_what_is_on_screen():
    one = ScreenContext(app="Safari", controls=[Control("Buy", "AXButton", 10, 10)])
    assert one.signature() == ScreenContext(app="Safari", controls=[Control("Buy", "AXButton", 10.2, 9.9)]).signature()
    assert one.signature() != ScreenContext(app="Safari", controls=[Control("Buy", "AXButton", 10, 80)]).signature()
    assert one.signature() != ScreenContext(app="Notes", controls=[Control("Buy", "AXButton", 10, 10)]).signature()


def test_the_screenshot_is_taken_at_key_release_while_speech_is_still_finishing():
    import threading

    from mcp_vision.buddy.controller import BuddyController

    order = []

    class Companion:
        def interrupt(self, token=None):
            pass

        def prefetch(self):
            order.append("screenshot")

    class Listener:
        def start(self):
            order.append("listening")

        def release(self):
            order.append("speech finishing")

        def cancel(self):
            pass

    class Quiet:
        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    controller = BuddyController(companion=Companion(), overlay=Quiet(), loop=loop, listener=Listener(),
                                 call_later=lambda delay, fn: None, on_main=lambda fn, *args: None, presenter=Quiet())
    controller.on_press()
    controller.on_release()
    loop.call_soon_threadsafe(loop.stop)
    assert order == ["listening", "screenshot", "speech finishing"]


def test_sentence_two_is_synthesized_while_sentence_one_plays():
    import threading

    from mcp_vision.buddy.speech_out import QueueSpeaker

    prepared_two = threading.Event()
    log = []

    class Voice:
        name = "test"

        def prepare(self, text):
            log.append(("prepare", text))
            if text == "Two.":
                prepared_two.set()
            return text

        def play(self, prepared, stop):
            if prepared == "One.":
                assert prepared_two.wait(2), "sentence two wasn't ready while one was playing"
            log.append(("play", prepared))

    speaker = QueueSpeaker(Voice())
    speaker.speak("One.")
    speaker.speak("Two.")
    asyncio.run(asyncio.wait_for(speaker.drain(), 5))
    assert log.index(("prepare", "Two.")) < log.index(("play", "One."))
    assert [entry for entry in log if entry[0] == "play"] == [("play", "One."), ("play", "Two.")]


def test_the_screen_map_is_read_at_key_release_too():
    reads = []

    class Page:
        def snapshot(self):
            reads.append(time.perf_counter())
            return ScreenContext(app="Safari", controls=[Control("Buy", "AXButton", 10, 10)])

    brain = ScriptedBrain("It's top left.")
    companion = plip(Page())
    companion.brain = brain
    released = time.perf_counter()
    companion.prefetch()                                   # key release
    time.sleep(0.3)                                        # last words still being finalized
    asyncio.run(companion.respond("where's buy"))
    assert len(reads) == 1 and reads[0] - released < 0.2  # walked once, at release
    assert "Buy | AXButton" in brain.calls[0][-1].text

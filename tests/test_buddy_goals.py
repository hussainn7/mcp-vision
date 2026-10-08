"""Tasks that take several steps: [GOAL]…[DONE], failures handed back, [DONE] checked, x,y clicks guarded."""
from __future__ import annotations

import asyncio

from buddy_fakes import Capturer, FakeHost, ScriptedBrain, Speaker
from mcp_vision.buddy.actions import ActionContext, ActionEngine
from mcp_vision.buddy.companion import CONTINUE_RE, Companion
from mcp_vision.buddy.pointing import GoalTag, ReplyStream, SpeechChunk
from mcp_vision.buddy.router import rule_route
from mcp_vision.buddy.screen_context import Control, ScreenContext


class Spotify(FakeHost):
    """Opening Spotify shows its window; clicking Play (where it is) starts the music; Buy does nothing."""

    def __init__(self, play_works=True):
        super().__init__()
        self.open_now, self.playing, self.play_works = False, False, play_works

    def open_app(self, path):
        super().open_app(path)
        self.open_now = True

    def click(self, x, y, button="left", count=1):
        self.calls.append(("click", round(x), round(y)))
        if self.play_works and (round(x), round(y)) == (300, 200):
            self.playing = True

    def snapshot(self):
        if not self.open_now:
            return ScreenContext(app="Finder", controls=[Control("New Folder", "button", 50, 50)])
        controls = [Control("Discover Weekly", "link", 300, 150), Control("Play", "button", 300, 200),
                    Control("Buy now", "button", 600, 400)]
        if self.playing:
            controls.append(Control("Pause", "button", 300, 260))
        return ScreenContext(app="Spotify", window="Discover Weekly", controls=controls)


def buddy(host, *replies, **kw):
    brain = ScriptedBrain(*replies)
    speaker = Speaker()
    companion = Companion(brain=brain, capturer=Capturer(), context=host, speaker=speaker,
                          actions=ActionEngine(ActionContext(host=host)), settle_interval=0.01, **kw)
    return companion, brain, speaker


def test_a_goal_keeps_going_after_the_open_until_done():
    host = Spotify()
    companion, brain, _ = buddy(host,
                                '[GOAL: play discover weekly] opening spotify. [DO:open_app {"name": "spotify"}]',
                                'hitting play. [DO:click {"id": 2}] playing it now. [DONE]')
    result = asyncio.run(companion.respond("open spotify and play my discover weekly"))
    # It stopped after the open before: nothing told it to look again.
    assert host.playing and result.finished and result.turns == 2 and result.outcome == "done"
    step = brain.calls[1][-1].text
    assert "(step 1 toward: play discover weekly" in step and "open_app: done" in step
    assert result.goal == "play discover weekly" and companion._goal == ""          # finished: the task is closed


def test_a_failed_step_goes_back_to_the_model_with_a_hint_and_isnt_said_twice():
    host = Spotify()
    host.open_now = True
    companion, brain, speaker = buddy(host,
                                      '[GOAL: play it] [DO:click {"id": 9}] playing it now.',
                                      'trying the play button by its name. [DO:click {"text": "Play"}] there. [DONE]')
    result = asyncio.run(companion.respond("play discover weekly"))
    step = brain.calls[1][-1].text
    assert "click failed: I can't find number 9 on screen anymore" in step
    assert "aim by its text or by x,y from the screenshot instead" in step              # for the model only
    assert host.playing and result.finished
    assert not any("number 9" in line or "playing it now" in line for line in speaker.said)   # muted, not lied


def test_done_after_a_click_that_changed_nothing_is_sent_back():
    host = Spotify(play_works=False)
    host.open_now = True
    companion, brain, _ = buddy(host,
                                '[GOAL: play it] [DO:click {"id": 2}] playing. [DONE]',
                                "that play button isn't responding, so it's not playing yet.")
    result = asyncio.run(companion.respond("play discover weekly"))
    assert "you ended with [DONE] right after clicking, but nothing on screen changed" in brain.calls[1][-1].text
    assert not result.finished and result.turns == 2


def test_a_click_by_x_y_on_buy_still_asks_first():
    host = Spotify()
    host.open_now = True
    companion, _, _ = buddy(host, '[GOAL: buy it] [DO:click {"x": 508, "y": 339}] buying it.')   # Buy now, by pixels
    result = asyncio.run(companion.respond("buy it"))
    assert result.pending == "Click “Buy now”" and not any(call[0] == "click" for call in host.calls)


def test_goal_tags_are_events_never_spoken_and_routing_spots_tasks():
    stream = ReplyStream()
    events = stream.feed("[GOAL: find remote jobs on indeed] pulling them up. ") + stream.close()
    assert events[0] == GoalTag("find remote jobs on indeed") and stream.goal == "find remote jobs on indeed"
    assert [e.text for e in events if isinstance(e, SpeechChunk)] == ["pulling them up."]
    assert rule_route("open spotify and play my discover weekly").multistep
    assert rule_route("find me some backend jobs on jobs.example").multistep
    assert not rule_route("open spotify").multistep and not rule_route("what's the capital of france").multistep
    assert CONTINUE_RE.match("okay keep going please") and not CONTINUE_RE.match("go on linkedin and find jobs")


def test_a_clock_on_the_page_doesnt_stop_it_settling():
    one = ScreenContext(app="Mail", controls=[Control("Inbox", "link", 40, 80), Control("2 min ago", "text", 300, 80)])
    later = ScreenContext(app="Mail", controls=[Control("Inbox", "link", 41, 80), Control("3 min ago", "text", 300, 80)])
    assert one.signature() != later.signature() and one.settle_signature() == later.settle_signature()

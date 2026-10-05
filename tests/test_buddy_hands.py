"""Plip's hands: click, scroll, press keys and drag, aimed by the numbered screen map."""
from __future__ import annotations

import asyncio

import pytest

from buddy_fakes import Capturer, FakeHost, Pointer, ScriptedBrain, Speaker, shot
from mcp_vision.buddy.actions import ActionContext, ActionEngine
from mcp_vision.buddy.actions.host import parse_keys
from mcp_vision.buddy.companion import Companion
from mcp_vision.buddy.screen_context import Control, ScreenContext

SPOTIFY = [Control("Search", "AXTextField", 300, 60), Control("Play", "AXButton", 610, 420),
           Control("Billie Jean", "AXRow", 500, 300)]


def run(coro):
    return asyncio.run(coro)


def engine(context=None, shots=None, observe=None):
    host = FakeHost()
    ctx = ActionContext(host=host, state={})
    ctx.screen = (shots if shots is not None else [shot()], context)
    if observe is not None:
        ctx.observe = observe
    return ActionEngine(ctx), host


def mapped(controls=SPOTIFY):
    context = ScreenContext(app="Spotify", controls=list(controls))
    context.describe([shot()])                       # numbers them, like the model sees
    return context


def test_keys_parse_shortcuts_and_zoom():
    assert parse_keys("cmd+t") == (17, 1 << 20)
    assert parse_keys("return") == (36, 0) and parse_keys("space") == (49, 0)
    assert parse_keys("cmd+=") == parse_keys("cmd++") == parse_keys("cmd+plus") == (24, 1 << 20)   # zoom in
    assert parse_keys("cmd+-") == parse_keys("cmd+minus") == (27, 1 << 20)                       # zoom out
    assert parse_keys("cmd+shift+t")[1] == (1 << 20) | (1 << 17)
    with pytest.raises(ValueError):
        parse_keys("cmd+banana")


def test_the_map_numbers_controls_and_finds_labels():
    context = mapped()
    text = context.describe([shot()])
    assert "[2] Play | AXButton" in text and context.ids[2].label == "Play"
    assert context.find("play").label == "Play" and context.find("billie").label == "Billie Jean"
    assert context.find("stop") is None


def test_click_by_number_or_label_and_double_or_right():
    context = mapped()
    actions, host = engine(context)
    assert run(actions.handle("click", {"id": 2})).status == "done"
    assert host.calls[-1] == ("click", 610, 420)
    actions, host = engine(context, observe=lambda: context)
    run(actions.handle("click", {"text": "Billie Jean", "double": True}))
    assert host.calls[-1] == ("click", 500, 300, "left", 2)
    run(actions.handle("click", {"text": "Search", "button": "right"}))
    assert host.calls[-1] == ("click", 300, 60, "right", 1)
    outcome = run(actions.handle("click", {"id": 99}))
    assert outcome.status == "failed" and "can't find number 99" in outcome.message


def test_risky_clicks_and_keys_ask_first():
    context = mapped([Control("Place your order", "AXButton", 700, 600)])
    actions, host = engine(context)
    outcome = run(actions.handle("click", {"id": 1}))
    assert outcome.status == "pending" and host.calls == []
    assert run(actions.answer(True)).status == "done" and host.calls == [("click", 700, 600)]
    assert run(actions.handle("press", {"keys": "cmd+q"})).status == "pending"
    assert run(actions.handle("press", {"keys": "cmd+="})).status == "done" and host.calls[-1] == ("press", "cmd+=")


def test_scroll_says_when_nothing_moved():
    still = mapped()
    actions, host = engine(still, observe=lambda: still)
    outcome = run(actions.handle("scroll", {"direction": "down"}))
    assert outcome.status == "done" and "nothing moved" in outcome.result.report
    pages = iter([mapped(), mapped([Control("Next song", "AXRow", 500, 200)])] * 5)
    actions, host = engine(mapped(), observe=lambda: next(pages))
    outcome = run(actions.handle("scroll", {"direction": "down", "amount": 2}))
    assert outcome.result.report == "scrolled down" and host.calls[-1][3] == 16          # two pages of lines
    assert actions.ctx.state["scrolled"] is True
    outcome = run(actions.handle("click", {"id": 2}))                                     # old numbers moved
    assert outcome.status == "failed" and "fresh look" in outcome.message


def test_scroll_to_reads_the_map_until_the_text_shows_up():
    screens = iter([mapped([Control(f"Song {n}", "AXRow", 500, 300)]) for n in range(1, 10)]
                   + [mapped([Control("Pricing", "AXHeading", 500, 300)])] * 3)
    actions, host = engine(mapped(), observe=lambda: next(screens))
    outcome = run(actions.handle("scroll_to", {"text": "pricing"}))
    assert outcome.status == "done" and "found 'Pricing'" in outcome.result.report
    assert sum(1 for call in host.calls if call[0] == "scroll") == 9


class Map:
    def __init__(self, context):
        self.context = context

    def snapshot(self):
        return self.context


def test_a_turn_clicks_play_when_asked():
    host = FakeHost()
    brain = ScriptedBrain('Playing it. [DO:click {"id": 2}]', "It's playing.")
    plip = Companion(brain=brain, capturer=Capturer(), speaker=Speaker(), pointer=Pointer(), walkthroughs=False,
                     context=Map(ScreenContext(app="Spotify", controls=list(SPOTIFY))),
                     actions=ActionEngine(ActionContext(host=host, state={})), settle_interval=0.01)
    result = run(plip.respond("click play and start the music"))
    assert result.state == "done" and result.did == ["Clicking"]
    assert ("click", 610, 420) in host.calls
    assert "[2] Play | AXButton" in brain.calls[0][-1].text           # the model saw the numbered map

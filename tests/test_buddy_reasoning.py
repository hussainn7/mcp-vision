"""Walkthrough sessions, screen context, change watching, and UI events."""
from __future__ import annotations

import asyncio

from PIL import Image

from mcp_vision.buddy.capture import ScreenCapturer
from mcp_vision.buddy.companion import Companion
from mcp_vision.buddy.geometry import Rect, ScreenInfo, Screenshot
from mcp_vision.buddy.pointing import DoneTag, PointTag, ReplyStream, SpeechChunk, StepsTag, extract_tags
from mcp_vision.buddy.prompt import system_prompt, user_turn_text
from mcp_vision.buddy.screen_context import Control, ScreenContext
from mcp_vision.buddy.watch import ScreenWatcher, difference, fingerprint

MONITORS = [{"left": 0, "top": 0, "width": 1512, "height": 982}]


def capturer():
    return ScreenCapturer(monitors=lambda: MONITORS, cursor=lambda: (10.0, 10.0), scale_factors=dict,
                          grabber=lambda m: Image.new("RGB", (m["width"] * 2, m["height"] * 2), "white"))


class ScriptedBrain:
    name = "scripted"
    label = "Claude"
    kind = "subscription"

    def __init__(self, replies, vision=True):
        self.replies = list(replies)
        self.vision = vision
        self.seen = []

    async def stream(self, *, system, turns, detailed=False):
        self.seen.append((system, turns))
        for chunk in self.replies.pop(0):
            yield chunk


class ScriptedWatcher:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def wait_for_change(self, timeout):
        self.calls += 1
        return self.outcomes.pop(0)


class Events:
    def __init__(self):
        self.items = []

    def __call__(self, kind, data):
        self.items.append((kind, data))

    def kinds(self, kind):
        return [data for k, data in self.items if k == kind]


def test_control_tags_are_events_and_never_spoken():
    reply = ReplyStream()
    events = reply.feed("[STEPS:3] First, open the File menu up top. [POINT:20,10:File menu] ") + reply.close()
    assert events[0] == StepsTag(3) and PointTag(20, 10, "File menu") in events
    assert reply.steps == 3 and not reply.done
    done = ReplyStream()
    out = done.feed("All set, you're finished. [DONE]") + done.close()
    assert out == [SpeechChunk("All set, you're finished."), DoneTag()] and done.done
    assert extract_tags("ok [STEPS:2] go [DONE]") == ("ok go", [])


def test_walkthrough_waits_for_each_step_and_finishes_on_done():
    brain = ScriptedBrain([
        ["[STEPS:3] First, open the File menu. [POINT:20,10:File menu]"],
        ["Nice. Now pick Export. [POINT:60,80:Export]"],
        ["Perfect, that's exported. [DONE]"],
    ])
    events = Events()
    watcher = ScriptedWatcher([True, True])
    result = asyncio.run(Companion(brain=brain, capturer=capturer(), observer=events, watcher=watcher).respond("export as pdf"))
    assert result.state == "done" and result.finished and result.turns == 3 and result.steps_total == 3
    assert watcher.calls == 2
    followup = brain.seen[1][1][-1].text
    assert "walkthrough check-in" in followup and "step 1 of 3" in followup
    walk = events.kinds("walkthrough")
    assert walk[0]["total"] == 3 and any(item.get("waiting") for item in walk)
    assert walk[-1].get("finished")
    assert [d["label"] for d in events.kinds("point")] == ["File menu", "Export"]


def test_walkthrough_stops_when_user_does_nothing_and_can_be_disabled():
    brain = ScriptedBrain([["[STEPS:2] Click the gear. [POINT:5,5:gear]"]])
    events = Events()
    result = asyncio.run(Companion(brain=brain, capturer=capturer(), observer=events,
                                   watcher=ScriptedWatcher([False])).respond("settings"))
    assert result.turns == 1 and not result.finished
    assert events.kinds("walkthrough")[-1].get("timed_out")
    brain = ScriptedBrain([["[STEPS:2] Click the gear."]])
    watcher = ScriptedWatcher([True])
    asyncio.run(Companion(brain=brain, capturer=capturer(), watcher=watcher, walkthroughs=False).respond("settings"))
    assert watcher.calls == 0


def test_observer_sees_the_whole_turn():
    events = Events()
    brain = ScriptedBrain([["Sure. Open settings. [POINT:30,30:Settings]"]])
    asyncio.run(Companion(brain=brain, capturer=capturer(), observer=events).respond("where are settings"))
    kinds = [kind for kind, _ in events.items]
    assert kinds[0] == "phase" and events.items[0][1]["phase"] == "thinking"
    assert "engine" in kinds and events.kinds("engine")[0] == {"label": "Claude", "kind": "subscription", "model": None}
    assert {s["id"] for s in events.kinds("step")} >= {"look", "think"}
    assert "".join(d["text"] for d in events.kinds("answer")) == "Sure. Open settings."
    assert kinds.index("answer") < kinds.index("done")


def test_screen_map_uses_screenshot_pixels_and_text_only_brains_get_no_images():
    screen = ScreenInfo(1, Rect(0, 0, 1512, 982), is_cursor_screen=True)
    shot = Screenshot(screen=screen, data=b"jpg", width=1280, height=831)
    context = ScreenContext(app="Keynote", window="Q4.key", selection="hello  world",
                            controls=[Control("File", "menu", 189, 12), Control("Off screen", "button", 9000, 10),
                                      Control("File", "menu", 189, 12)])
    text = context.describe([shot])
    assert 'frontmost app: Keynote (window "Q4.key")' in text and 'selected text: "hello world"' in text
    assert "File | menu | 160,10" in text and "Off screen" not in text and text.count("File |") == 1
    turn = user_turn_text("where is file", [shot], context)
    assert turn.endswith("the user said: where is file") and "controls on screen" in turn
    assert "(image dimensions" in user_turn_text("q", [shot], context, vision=False)
    assert "can't see the screenshot" in system_prompt(vision=False)

    brain = ScriptedBrain([["Top left. [POINT:160,10:File]"]], vision=False)

    class FixedContext:
        def snapshot(self):
            return context

    asyncio.run(Companion(brain=brain, capturer=capturer(), context=FixedContext()).respond("where is file"))
    system, turns = brain.seen[0]
    assert turns[-1].images == () and "File | menu" in turns[-1].text and "can't see" in system


def test_watcher_needs_a_change_that_then_settles():
    a = fingerprint(Image.new("RGB", (300, 200), "white"))
    b = fingerprint(Image.new("RGB", (300, 200), "black"))
    assert difference(a, a) == 0 and difference(a, b) == 255

    async def run(frames, timeout=10.0):
        frames = iter(frames)
        clock = [0.0]

        async def sleep(seconds):
            clock[0] += seconds

        watcher = ScreenWatcher(lambda: next(frames), interval=0.5, clock=lambda: clock[0], sleep=sleep)
        return await watcher.wait_for_change(timeout)

    assert asyncio.run(run([a, a, b, b])) is True                       # changed, then held still
    assert asyncio.run(run([a] * 30, timeout=3)) is False               # nothing happened
    assert asyncio.run(run([a, b, a, b, a, b, a, b, b], timeout=10)) is True   # flicker until it settles


def test_capturer_fingerprint_is_small():
    assert len(capturer().fingerprint()) == 64 * 40

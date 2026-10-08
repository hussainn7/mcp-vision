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


class FakeAX:
    """Just enough of ApplicationServices for the screen map walk: elements are dicts with a frame."""

    kAXValueCGPointType, kAXValueCGSizeType = "point", "size"

    def AXUIElementCopyAttributeValue(self, element, attribute, _):
        if attribute in {"AXPosition", "AXSize"} and "frame" in element:
            return 0, (attribute, element["frame"])
        return (0, element[attribute]) if attribute in element else (-25212, None)

    def AXValueGetValue(self, value, kind, _):
        from types import SimpleNamespace

        x, y, width, height = value[1]
        return True, SimpleNamespace(x=x, y=y) if kind == "point" else SimpleNamespace(width=width, height=height)


def ax(role, frame=None, title="", children=()):
    node = {"AXRole": role, "AXChildren": list(children)}
    if frame is not None:
        node["frame"] = frame
    if title:
        node["AXTitle"] = title
    return node


def test_the_screen_map_only_lists_what_is_actually_on_screen():
    from mcp_vision.buddy.ax_context import MacAXContext

    # A full-screen Chrome window scrolled down a page, as this Mac's Accessibility tree reports it: the hidden
    # toolbar sits above the screen, its tabs are listed twice, the skip link is a 1x1 dot, links scrolled off
    # the top are pinned to the page's top edge as slivers, and a list scrolled inside the page hides its rows.
    tab = ax("AXRadioButton", (380, -60, 60, 30), "Recordings tab")
    toolbar = ax("AXToolbar", (0, -100, 1512, 63), children=[ax("AXButton", (20, -90, 30, 30), "Back"), tab, tab])
    inner = ax("AXScrollArea", (800, 400, 300, 200), children=[
        ax("AXList", (800, 100, 300, 800), children=[ax("AXButton", (820, 150, 80, 30), "Row above"),
                                                    ax("AXButton", (820, 450, 80, 30), "Row shown")])])
    page = ax("AXWebArea", (0, 37, 1512, 945), children=[
        ax("AXLink", (0, 37, 1, 1), "Skip to content"),
        ax("AXLink", (250, 37, 60, 1.5), "1 Branch"),
        ax("AXButton", (500, 20, 100, 40), "Half scrolled"),
        ax("AXButton", (1300, 120, 100, 30), "Invite"),
        inner])
    window = ax("AXWindow", (0, 37, 1512, 945), "PostHog", children=[toolbar, ax("AXScrollArea", (0, 37, 1512, 945),
                                                                                  children=[page])])
    menubar = ax("AXMenuBar", (0, 0, 1512, 37), children=[ax("AXMenuBarItem", (60, 4, 40, 28), "File")])

    controls = MacAXContext()._walk(FakeAX(), [menubar, window])

    assert [c.label for c in controls] == ["File", "Half scrolled", "Invite", "Row shown"]
    half = next(c for c in controls if c.label == "Half scrolled")
    assert (half.x, half.y) == (550, 48.5)          # the middle of the part on screen, so a click lands on it


def test_the_screen_map_says_where_typing_goes_but_never_what_is_typed():
    from mcp_vision.buddy.ax_context import _focused

    search = {"AXRole": "AXTextField", "AXDescription": "Address and search bar", "AXValue": "secret query"}
    password = {"AXRole": "AXTextField", "AXSubrole": "AXSecureTextField", "AXTitle": "Password", "AXValue": "hunter2"}
    assert _focused(FakeAX(), search) == 'text field "Address and search bar"'
    assert _focused(FakeAX(), password) == 'password field "Password"'
    assert _focused(FakeAX(), {"AXRole": "AXButton", "AXTitle": "OK"}) == ""
    screen = ScreenInfo(1, Rect(0, 0, 1512, 982), is_cursor_screen=True)
    text = ScreenContext(app="Chrome", focused=_focused(FakeAX(), search)).describe(
        [Screenshot(screen=screen, data=b"jpg", width=1280, height=831)])
    assert 'typing goes into: text field "Address and search bar"' in text and "secret" not in text


def test_a_walkthrough_is_routed_once_and_keeps_its_effort_and_screen_every_step():
    from mcp_vision.buddy.companion import Route
    from mcp_vision.buddy.router import rule_route

    class Router:
        """Like Jev: the user's question is a deep one, Plip's own check-in words read as a quick one."""

        def __init__(self):
            self.heard = []

        async def route(self, transcript, screens):
            self.heard.append(transcript)
            return Route(needs_screen=True, detailed=len(self.heard) == 1, provider="jev", latency_ms=120)

    class Brain(ScriptedBrain):
        async def stream(self, *, system, turns, detailed=False):
            self.depths = [*getattr(self, "depths", []), detailed]
            async for chunk in super().stream(system=system, turns=turns, detailed=detailed):
                yield chunk

    brain = Brain([["[STEPS:3] First, open the File menu. [POINT:20,10:File menu]"],
                   ["Nice. Now pick Export. [POINT:60,80:Export]"], ["Perfect, that's exported. [DONE]"]])
    router = Router()
    result = asyncio.run(Companion(brain=brain, capturer=capturer(), router=router,
                                   watcher=ScriptedWatcher([True, True])).respond("walk me through exporting a pdf"))
    assert result.finished and result.turns == 3
    assert router.heard == ["walk me through exporting a pdf"]      # check-ins aren't routed as if the user spoke
    assert brain.depths == [True, True, True]                       # one effort for the whole task
    assert all(turns[-1].images for _, turns in brain.seen)          # every check-in sees the screen it talks about

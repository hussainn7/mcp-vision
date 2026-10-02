"""Platform-neutral tests for the buddy: geometry, tags, flight, prompt, brain request, turn loop."""
from __future__ import annotations

import asyncio
import base64
import math

import pytest
from PIL import Image

from mcp_vision.buddy.brain_claude import ClaudeBrain
from mcp_vision.buddy.capture import ScreenCapturer
from mcp_vision.buddy.companion import Companion, Route, Target, resolve_target
from mcp_vision.buddy.conversation import Conversation, Turn
from mcp_vision.buddy.flight import FlightPlan, ease_in_out_cubic, flight_duration
from mcp_vision.buddy.geometry import (
    Rect, ScreenInfo, Screenshot, fit_within, from_appkit, order_cursor_first, to_appkit,
)
from mcp_vision.buddy.pointing import PointTag, ReplyStream, SpeechChunk, extract_tags, parse_tag
from mcp_vision.buddy.prompt import SYSTEM_PROMPT, user_turn_text

MONITORS = [
    {"left": 0, "top": 0, "width": 1512, "height": 982},
    {"left": 1512, "top": -200, "width": 2560, "height": 1440},
]


def capturer(cursor=(2000.0, 100.0)) -> ScreenCapturer:
    return ScreenCapturer(
        monitors=lambda: MONITORS,
        grabber=lambda m: Image.new("RGB", (m["width"] * 2, m["height"] * 2), "white"),
        cursor=lambda: cursor, scale_factors=dict,
    )


def shot(index=1, frame=Rect(0, 0, 1512, 982), size=(1280, 831), cursor=True) -> Screenshot:
    screen = ScreenInfo(index=index, frame=frame, scale=2.0, is_cursor_screen=cursor)
    return Screenshot(screen=screen, data=b"\xff\xd8jpeg", width=size[0], height=size[1])


# -- geometry ------------------------------------------------------------------

def test_fit_within_downscales_longest_edge_only():
    assert fit_within(3024, 1964, 1280) == (1280, 831)
    assert fit_within(800, 600, 1280) == (800, 600)
    assert fit_within(1964, 3024, 1280) == (831, 1280)


def test_screenshot_pixels_map_to_global_points_on_secondary_display():
    s = shot(index=2, frame=Rect(1512, -200, 2560, 1440), size=(1280, 720))
    assert s.to_global(640, 360) == (2792.0, 520.0)
    assert s.to_global(0, 0) == (1513.0, -199.0)          # clamped one point inside
    gx, gy = s.to_global(100, 50)
    assert s.from_global(gx, gy) == pytest.approx((100, 50))


def test_appkit_conversion_round_trips():
    assert to_appkit(10, 100, 982) == (10, 882)
    assert from_appkit(*to_appkit(10, 100, 982), 982) == (10, 100)


def test_capture_puts_cursor_screen_first_and_encodes_jpeg():
    shots = capturer().capture()
    assert [s.screen.index for s in shots] == [2, 1]
    assert shots[0].screen.is_cursor_screen and not shots[1].screen.is_cursor_screen
    assert (shots[0].width, shots[0].height) == (1280, 720)
    assert shots[0].data[:2] == b"\xff\xd8"
    only = capturer().capture(only_cursor_screen=True)
    assert [s.screen.index for s in only] == [2]


def test_capture_defaults_cursor_screen_when_pointer_unknown():
    screens = capturer(cursor=None).screens()
    assert screens[0].is_cursor_screen
    assert order_cursor_first(screens)[0].index == 1


# -- tags ------------------------------------------------------------------------

def test_parse_tag_variants():
    assert parse_tag("[POINT:10,20:Save button]") == PointTag(10, 20, "Save button")
    assert parse_tag("[POINT: 10 , 20 : File menu : screen2 ]") == PointTag(10, 20, "File menu", 2)
    assert parse_tag("[POINT:10.5,20]") == PointTag(10.5, 20, "")
    assert parse_tag("[point:none]") is None
    assert parse_tag("[not a tag]") is None


def test_extract_tags_strips_tags_and_markdown():
    text, tags = extract_tags("**Click** the gear [POINT:5,6:Settings gear] then `Save`.")
    assert text == "Click the gear then Save."
    assert tags == [PointTag(5, 6, "Settings gear")]


def stream_events(text: str, step: int) -> list:
    reply = ReplyStream()
    events = []
    for i in range(0, len(text), step):
        events += reply.feed(text[i:i + step])
    return events + reply.close()


@pytest.mark.parametrize("step", [1, 3, 7, 50, 1000])
def test_reply_stream_orders_tags_before_their_sentence_for_any_chunking(step):
    text = ("Sure thing. The export option lives in the File menu up top [POINT:220,96:File menu:screen2], "
            "so open that first. Then choose Export as PDF [POINT:640,412:Export as PDF]. Done!")
    events = stream_events(text, step)
    assert events == [
        SpeechChunk("Sure thing."),
        PointTag(220, 96, "File menu", 2),
        SpeechChunk("The export option lives in the File menu up top, so open that first."),
        PointTag(640, 412, "Export as PDF"),
        SpeechChunk("Then choose Export as PDF."),
        SpeechChunk("Done!"),
    ]


def test_reply_stream_keeps_ordinary_brackets_and_tail_without_punctuation():
    events = stream_events("Use the [Beta] channel for that, it is newer", 4)
    assert events == [SpeechChunk("Use the [Beta] channel for that, it is newer")]


def test_reply_stream_releases_sentences_before_stream_ends():
    reply = ReplyStream()
    early = reply.feed("This first sentence is long enough to speak. And then")
    assert early == [SpeechChunk("This first sentence is long enough to speak.")]
    assert reply.close() == [SpeechChunk("And then")]


def test_reply_stream_tag_at_end_is_flushed():
    assert stream_events("Right there. [POINT:1,2:Here]", 5) == [
        SpeechChunk("Right there."), PointTag(1, 2, "Here")]


def test_reply_stream_does_not_treat_unterminated_bracket_as_tag_forever():
    events = stream_events("[" + "x" * 300 + ". ok then", 10)
    assert events and events[0].text.startswith("[xxx")


# -- flight ------------------------------------------------------------------------

def test_flight_starts_and_ends_exactly_and_bows_upward():
    plan = FlightPlan.between((100, 500), (900, 500))
    first, last = plan.frame_at(0), plan.frame_at(plan.duration)
    assert (first.x, first.y) == (100, 500) and (last.x, last.y) == pytest.approx((900, 500))
    mid = plan.frame_at(plan.duration / 2)
    assert mid.y < 500                       # arc rises (y up is negative)
    assert mid.scale == pytest.approx(1.35, abs=0.01)
    assert first.scale == pytest.approx(1.0) and last.scale == pytest.approx(1.0)


def test_flight_rotation_follows_travel_and_duration_is_bounded():
    plan = FlightPlan.between((0, 0), (0, 600))      # straight down
    assert plan.frame_at(plan.duration).angle == pytest.approx(90, abs=30)
    assert flight_duration(0) == 0.45 and flight_duration(10_000) == 1.15
    assert ease_in_out_cubic(0) == 0 and ease_in_out_cubic(1) == 1
    zero = FlightPlan.between((5, 5), (5, 5))
    assert all(math.isfinite(f.x) for f in zero.frames())


# -- prompt and brain request ------------------------------------------------------

def test_system_prompt_documents_point_protocol():
    assert "[POINT:x,y:label]" in SYSTEM_PROMPT and ":screen2]" in SYSTEM_PROMPT
    text = user_turn_text("where is export", [shot(), shot(index=2, cursor=False)])
    assert "screen1: 1280x831 pixels, the cursor is on this screen" in text
    assert "screen2" in text and text.endswith("The user said: where is export")
    assert "No screenshot" in user_turn_text("hi", [])


def test_claude_request_caches_system_and_attaches_labeled_images():
    brain = ClaudeBrain(client=object())
    s = shot()
    body = brain.request(system="SYS", turns=[Turn("user", "old q"), Turn("assistant", "old a"),
                                              Turn("user", "now", images=(s,))])
    assert body["model"] == "claude-opus-5-5"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["fallbacks"] == "default" and body["betas"] == ["server-side-fallback-2026-07-01"]
    assert body["output_config"] == {"effort": "low"}
    assert "thinking" not in body and "temperature" not in body
    assert body["messages"][0] == {"role": "user", "content": "old q"}
    current = body["messages"][-1]["content"]
    assert current[0] == {"type": "text", "text": "screen1"}
    assert current[1]["source"]["data"] == base64.standard_b64encode(s.data).decode()
    assert current[-1] == {"type": "text", "text": "now"}


# -- conversation ------------------------------------------------------------------

def test_conversation_keeps_whole_exchanges():
    convo = Conversation(max_turns=4)
    for i in range(5):
        convo.record(f"q{i}", f"a{i}")
    assert [t.text for t in convo.history()] == ["q3", "a3", "q4", "a4"]
    convo.record("", "ignored")
    assert len(convo.history()) == 4


# -- the turn loop -----------------------------------------------------------------

class FakeBrain:
    name = "fake"

    def __init__(self, chunks, delay=0.0):
        self.chunks = chunks
        self.delay = delay
        self.calls = []

    async def stream(self, *, system, turns, detailed=False):
        self.calls.append(turns)
        self.detailed = detailed
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk


class FakeSpeaker:
    def __init__(self):
        self.said, self.stopped = [], 0

    def speak(self, text):
        self.said.append(text)

    async def drain(self):
        return None

    def stop(self):
        self.stopped += 1


class FakePointer:
    def __init__(self):
        self.events = []

    def set_state(self, state, detail=""):
        self.events.append(("state", state))

    def point(self, x, y, label):
        self.events.append(("point", round(x), round(y), label))

    def release(self):
        self.events.append(("release",))


class FixedRouter:
    def __init__(self, route):
        self.route_value = route

    async def route(self, transcript, screens):
        return self.route_value


def test_turn_points_on_the_right_screen_and_speaks_clean_text():
    brain = FakeBrain(["Open the File menu ", "[POINT:640,360:File menu:screen2]", " up top. ",
                       "Then pick Export."])
    speaker, pointer = FakeSpeaker(), FakePointer()
    buddy = Companion(brain=brain, capturer=capturer(), speaker=speaker, pointer=pointer)
    result = asyncio.run(buddy.respond("  where is   export "))
    assert result.state == "done"
    assert result.targets == [Target(x=2792.0, y=520.0, label="File menu", screen=2)]
    assert speaker.said == ["Open the File menu up top.", "Then pick Export."]
    assert ("point", 2792, 520, "File menu") in pointer.events
    assert pointer.events[-1] == ("state", "idle")
    user_turn = brain.calls[0][-1]
    assert user_turn.text.endswith("The user said: where is export") and len(user_turn.images) == 2
    history = buddy.conversation.history()
    assert history[0].text == "where is export"
    assert "pointed at: File menu on screen2" in history[1].text
    assert {"looked", "first_token", "first_speech", "first_point", "spoken"} <= set(result.timings)


def test_router_can_skip_screenshots_for_general_questions():
    brain = FakeBrain(["Paris is the capital of France."])
    buddy = Companion(brain=brain, capturer=capturer(), router=FixedRouter(Route(needs_screen=False)))
    result = asyncio.run(buddy.respond("what's the capital of france"))
    assert brain.calls[0][-1].images == ()
    assert result.spoken == "Paris is the capital of France."


def test_out_of_bounds_or_unknown_screen_tags_are_ignored():
    shots = capturer().capture()
    assert resolve_target(PointTag(5000, 10, "x"), shots) is None
    assert resolve_target(PointTag(10, 10, "x", screen=9), shots) is None
    assert resolve_target(PointTag(10, 10, "x"), []) is None
    assert resolve_target(PointTag(0, 0, "x"), shots).screen == 2


def test_brain_failure_is_spoken_not_silent():
    class Broken:
        name = "broken"

        async def stream(self, *, system, turns, detailed=False):
            raise RuntimeError("HTTP 401 invalid x-api-key")
            yield  # pragma: no cover

    speaker = FakeSpeaker()
    result = asyncio.run(Companion(brain=Broken(), capturer=capturer(), speaker=speaker).respond("hi"))
    assert result.state == "error" and "API key" in result.error
    assert speaker.said == [result.error]


def test_new_question_interrupts_the_current_answer():
    async def scenario():
        speaker, pointer = FakeSpeaker(), FakePointer()
        buddy = Companion(brain=FakeBrain(["one. "] * 50, delay=0.01), capturer=capturer(),
                          speaker=speaker, pointer=pointer)
        first = asyncio.ensure_future(buddy.respond("long answer please"))
        await asyncio.sleep(0.05)
        buddy.interrupt()
        return await first, speaker, pointer

    result, speaker, pointer = asyncio.run(scenario())
    assert result.state == "cancelled"
    assert speaker.stopped >= 1 and ("release",) in pointer.events


def test_empty_transcript_does_not_call_the_model():
    brain = FakeBrain(["never"])
    result = asyncio.run(Companion(brain=brain, capturer=capturer()).respond("   "))
    assert result.state == "error" and brain.calls == []

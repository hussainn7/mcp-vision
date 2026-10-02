"""Platform-neutral tests for the buddy: geometry, tags, flight, prompt, brain request, turn loop."""
from __future__ import annotations

import asyncio
import base64

import pytest
from PIL import Image

from mcp_vision.buddy.brain_claude import ClaudeBrain
from mcp_vision.buddy.capture import ScreenCapturer
from mcp_vision.buddy.companion import Companion, Route, Target, resolve_target
from mcp_vision.buddy.conversation import Conversation, Turn
from mcp_vision.buddy.animator import BuddyAnimator
from mcp_vision.buddy.flight import REST_ANGLE, FlightPlan, flight_duration, smoothstep
from mcp_vision.buddy.geometry import (
    Rect, ScreenInfo, Screenshot, fit_within, from_appkit, order_cursor_first, to_appkit,
)
from mcp_vision.buddy.pointing import PointTag, ReplyStream, SpeechChunk, extract_tags, parse_tag
from mcp_vision.buddy.prompt import SYSTEM_PROMPT, screen_label, user_turn_text

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


PRIMARY = Rect(0, 0, 1512, 982)


def shot(index=1, frame=PRIMARY, size=(1280, 831), cursor=True) -> Screenshot:
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


@pytest.mark.parametrize("step", [1, 4, 100])
def test_malformed_or_truncated_point_tags_are_never_spoken(step):
    events = stream_events("Click it. [POINT: x=10, y=20] Then save. [POINT:5,", step)
    assert events == [SpeechChunk("Click it. Then save.")]
    assert extract_tags("ok [POINT:ten,twenty:x] done") == ("ok done", [])


def test_reply_stream_does_not_treat_unterminated_bracket_as_tag_forever():
    events = stream_events("[" + "x" * 300 + ". ok then", 10)
    assert events and events[0].text.startswith("[xxx")


# -- flight ------------------------------------------------------------------------

def test_flight_starts_and_ends_exactly_and_bows_upward():
    plan = FlightPlan.between((100, 500), (900, 500))
    assert plan.control == (500, 420)                 # min(800 * 0.2, 80) above the midpoint
    assert plan.duration == 1.0                       # 800 pt / 800
    first, last = plan.frame_at(0), plan.frame_at(plan.duration)
    assert (first.x, first.y) == (100, 500) and (last.x, last.y) == pytest.approx((900, 500))
    assert last.done and not first.done
    mid = plan.frame_at(plan.duration / 2)
    assert mid.y < 500 and mid.scale == pytest.approx(1.3)
    assert first.scale == pytest.approx(1.0) and last.scale == pytest.approx(1.0)


def test_flight_rotation_faces_travel_and_duration_is_bounded():
    right = FlightPlan.between((0, 0), (600, 0))
    assert right.frame_at(right.duration / 2).angle == pytest.approx(90, abs=1)   # tip points right
    down = FlightPlan.between((0, 0), (0, 600))
    assert down.frame_at(down.duration).angle == pytest.approx(180, abs=1)
    assert flight_duration(0) == 0.6 and flight_duration(10_000) == 1.4
    assert smoothstep(0) == 0 and smoothstep(1) == 1 and smoothstep(0.5) == 0.5
    zero = FlightPlan.between((5, 5), (5, 5))
    frame = zero.frame_at(0.3)
    assert (frame.x, frame.y) == (5, 5) and frame.angle == REST_ANGLE


# -- animator ----------------------------------------------------------------------

def run_frames(animator, start, seconds, mouse=(100, 100), fps=60):
    state = None
    for i in range(int(seconds * fps)):
        state = animator.tick(start + i / fps, mouse)
    return state, start + seconds


def test_animator_follows_cursor_with_offset():
    animator = BuddyAnimator()
    state, _ = run_frames(animator, 0.0, 0.5, mouse=(400, 300))
    assert state.mode == "follow"
    assert (state.x, state.y) == pytest.approx((435, 325), abs=0.5)
    assert state.angle == pytest.approx(REST_ANGLE, abs=0.5)


def test_animator_full_point_cycle_types_label_holds_and_returns():
    screen = Rect(0, 0, 1512, 982)
    animator = BuddyAnimator(screens=lambda: [screen])
    run_frames(animator, 0.0, 0.2, mouse=(100, 100))
    animator.point(1505, 975, "Save button")         # near the corner: clamped 20 pt inside
    t = 0.2
    seen_modes, bubbles = set(), []
    for i in range(int(8 * 60)):
        state = animator.tick(t + i / 60, (100, 100))
        seen_modes.add(state.mode)
        if state.mode == "pointing":
            bubbles.append(state.bubble)
            assert (state.x, state.y) == pytest.approx((1492, 962))
    assert {"fly_out", "pointing", "fly_back", "follow"} <= seen_modes
    assert "Save button" in bubbles and bubbles[0] == "S"
    assert state.mode == "follow" and not animator.busy


def test_animator_chains_targets_then_release_flies_home():
    animator = BuddyAnimator()
    run_frames(animator, 0.0, 0.1)
    animator.point(800, 200, "File menu")
    animator.point(900, 600, "Export")
    completed = []
    for i in range(int(6 * 60)):
        state = animator.tick(0.1 + i / 60, (100, 100))
        if state.mode == "pointing" and state.bubble in {"File menu", "Export"} and state.bubble not in completed:
            completed.append(state.bubble)
    assert completed == ["File menu", "Export"]
    animator.point(500, 500, "Later")
    animator.release()
    state = animator.tick(10.0, (100, 100))
    assert state.mode in {"fly_back", "follow"} and not animator._queue


def test_animator_return_flight_cancels_when_mouse_moves_far():
    animator = BuddyAnimator()
    run_frames(animator, 0.0, 0.1)
    animator.point(900, 900, "x")
    t = 0.1
    while animator.mode != "fly_back":
        animator.tick(t, (100, 100))
        t += 1 / 60
    state = animator.tick(t + 0.01, (400, 400))       # user grabbed the mouse
    assert state.mode == "follow" and (state.x, state.y) == (435, 425)


def test_animator_keeps_pointing_while_still_speaking():
    animator = BuddyAnimator()
    run_frames(animator, 0.0, 0.1)
    animator.set_voice("speaking")
    animator.point(600, 400, "Go")
    t = 0.1
    for i in range(int(6 * 60)):
        state = animator.tick(t + i / 60, (100, 100))
    assert state.mode == "pointing"                   # 3 s hold extended by speech
    animator.set_voice("idle")
    for i in range(int(2 * 60)):
        state = animator.tick(t + 6 + i / 60, (100, 100))
    assert state.mode in {"fly_back", "follow"}


def test_level_meter_attacks_fast_and_decays():
    animator = BuddyAnimator()
    animator.set_level(0.8)
    animator.set_level(0.0)
    assert animator.level == pytest.approx(0.8 * 0.72)


# -- prompt and brain request ------------------------------------------------------

def test_system_prompt_documents_point_protocol():
    assert "[POINT:x,y:label]" in SYSTEM_PROMPT and ":screen2]" in SYSTEM_PROMPT
    assert "primary focus" in SYSTEM_PROMPT and "image dimensions" not in SYSTEM_PROMPT
    assert user_turn_text("where is export", [shot()]) == "where is export"
    assert "no screenshot" in user_turn_text("hi", [])


def test_screen_labels_match_prompt_vocabulary():
    assert screen_label(shot(), 1) == "the user's screen (cursor is here) (image dimensions: 1280x831 pixels)"
    assert screen_label(shot(), 2) == ("screen1 of 2, cursor is on this screen (primary focus) "
                                       "(image dimensions: 1280x831 pixels)")
    assert screen_label(shot(index=2, cursor=False), 2).startswith("screen2 of 2, secondary screen")


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
    assert current[0]["source"]["data"] == base64.standard_b64encode(s.data).decode()
    assert current[1]["text"].startswith("the user's screen (cursor is here)")
    assert current[-1] == {"type": "text", "text": "now"}
    assert brain.request(system="S", turns=[Turn("user", "q")], detailed=True)["output_config"] == {
        "effort": "medium"}


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
    assert user_turn.text == "where is export" and len(user_turn.images) == 2
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


def test_prefetched_screens_are_reused_once_and_expire():
    calls = []
    base = capturer()

    class Counting:
        def screens(self):
            return base.screens()

        def capture(self, **kwargs):
            calls.append(1)
            return base.capture(**kwargs)

    now = [0.0]
    buddy = Companion(brain=FakeBrain(["ok."]), capturer=Counting(), clock=lambda: now[0])
    buddy.prefetch()
    asyncio.run(buddy.respond("what is this"))
    assert len(calls) == 1                      # the prefetch served the turn
    buddy.prefetch()
    now[0] = 10.0                               # stale by the time the transcript lands
    asyncio.run(buddy.respond("and this"))
    assert len(calls) == 3


def test_cli_ask_with_image_runs_headless(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from mcp_vision.buddy import cli as buddy_cli
    from mcp_vision.buddy import factory

    image = tmp_path / "screen.png"
    Image.new("RGB", (2560, 1600), "white").save(image)
    brain = FakeBrain(["The save button is top left. [POINT:40,20:save button]"])
    real = factory.make_companion
    monkeypatch.setattr(factory, "make_companion", lambda settings, **kw: real(settings, brain=brain, **kw))
    monkeypatch.setenv("BUDDY_ROUTER", "rules")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    result = CliRunner().invoke(buddy_cli.buddy, ["ask", "--image", str(image), "--json", "where is save"])
    assert result.exit_code == 0, result.output
    import json as _json

    payload = _json.loads(result.output)
    assert payload["spoken"] == "The save button is top left."
    assert payload["targets"][0]["label"] == "save button"
    assert payload["targets"][0]["x"] == pytest.approx(40 * 2560 / 1280)


def test_empty_transcript_does_not_call_the_model():
    brain = FakeBrain(["never"])
    result = asyncio.run(Companion(brain=brain, capturer=capturer()).respond("   "))
    assert result.state == "error" and brain.calls == []

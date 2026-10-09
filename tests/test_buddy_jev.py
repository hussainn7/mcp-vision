"""Jev client contract, Jev/rules routing, element snapping, and the speech queue."""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from mcp_vision.buddy.companion import Target
from mcp_vision.buddy.geometry import Rect, ScreenInfo
from mcp_vision.buddy.jev import Choice, JevClient, JevError, JevResult, Noul, Score
from mcp_vision.buddy.router import INTENTS, JevRouter, RuleRouter, rule_route
from mcp_vision.buddy.snap import Element, ElementSnapper, HeuristicChooser, JevChooser
from mcp_vision.buddy.speech_out import ElevenLabsVoice, PrintVoice, QueueSpeaker

# Shape recorded from a live /v1/systemone response (typesafe-ai/system-one-adapter-python).
LIVE = {"model": "speed_v12_snowy_flower", "usage": {"input_tokens": 448, "output_tokens": 55},
        "answers": {
            "positive": {"type": "noul", "noul": 0.98},
            "rating": {"type": "score", "score": 4.0, "confidence": 1.0, "legend": {"0": "bad", "4": "great"},
                       "probabilities": {"0": 0.0, "1": 0.0, "2": 0.0, "3": 0.0, "4": 1.0}},
            "genre": {"type": "choice", "choice": "fiction", "confidence": 1.0,
                      "probabilities": {"fiction": 1.0, "nonfiction": 0.0}}}}


class Recorder:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, json.loads(body), timeout))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        status, payload, *extra = item
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return status, (extra[0] if extra else {}), raw


def client(*responses) -> tuple[JevClient, Recorder]:
    recorder = Recorder(*responses)
    return JevClient("ts-key", transport=recorder, sleep=lambda _s: None), recorder


# -- client --------------------------------------------------------------------

def test_request_matches_published_contract_and_parses_live_shape():
    jev, rec = client((200, LIVE))
    result = jev.ask_sync({"text": "review"}, {
        "positive": Noul(instructions="Is it positive?"),
        "rating": Score(criteria=["bad", "meh", "ok", "good", "great"]),
        "genre": Choice(criteria={"fiction": "made up", "nonfiction": "true"}, instructions="Genre?"),
    })
    url, headers, body, timeout = rec.calls[0]
    assert url == "https://api.typesafe.ai/v1/systemone"
    assert headers["Authorization"] == "Bearer ts-key" and timeout == 2.5
    assert body == {"model": "jev-latest", "state": {"text": "review"}, "questions": {
        "positive": {"type": "noul", "instructions": "Is it positive?"},
        "rating": {"type": "score", "criteria": ["bad", "meh", "ok", "good", "great"]},
        "genre": {"type": "choice", "criteria": {"fiction": "made up", "nonfiction": "true"},
                  "instructions": "Genre?"}}}
    assert result.noul("positive") == 0.98 and result.score("rating") == 4.0
    genre = result.choice("genre", {"fiction", "nonfiction"})
    assert genre.choice == "fiction" and genre.p("fiction") == 1.0
    assert result.model == "speed_v12_snowy_flower" and result.usage["input_tokens"] == 448


def test_rounded_probabilities_are_renormalized_not_rejected():
    result = JevResult(answers={"x": {"type": "choice", "choice": "a", "confidence": 0.7,
                                      "probabilities": {"a": 0.67, "b": 0.17, "c": 0.17}}})
    answer = result.choice("x", {"a", "b", "c"})
    assert sum(answer.probabilities.values()) == pytest.approx(1.0)


@pytest.mark.parametrize("answer", [
    {"type": "choice", "choice": "zzz", "probabilities": {"a": 1.0}},
    {"type": "choice", "choice": "a", "probabilities": {"a": float("nan")}},
    {"type": "choice", "choice": "a"},
    {"type": "noul", "noul": 1.5},
])
def test_malformed_answers_raise(answer):
    result = JevResult(answers={"x": answer})
    with pytest.raises(JevError):
        result.noul("x") if answer["type"] == "noul" else result.choice("x", {"a"})


def test_retryable_status_is_retried_once_then_raises_with_status():
    jev, rec = client((529, {"detail": "overloaded"}), (200, LIVE))
    assert jev.ask_sync("s", {"positive": Noul()}).noul("positive") == 0.98
    assert len(rec.calls) == 2
    jev, _ = client((429, {}, {"retry-after-ms": "50"}), (429, {}))
    with pytest.raises(JevError) as caught:
        jev.ask_sync("s", {"positive": Noul()})
    assert caught.value.status == 429


def test_validation_error_is_not_retried_and_detail_is_surfaced():
    jev, rec = client((422, {"detail": [{"loc": ["body"], "msg": "field required", "type": "missing"}]}))
    with pytest.raises(JevError, match="HTTP 422: field required"):
        jev.ask_sync("s", {"positive": Noul()})
    assert len(rec.calls) == 1


def test_network_errors_become_jev_errors():
    jev, rec = client(ConnectionResetError(), TimeoutError())
    with pytest.raises(JevError, match="network error"):
        jev.ask_sync("s", {"positive": Noul()})
    assert len(rec.calls) == 2


def test_choice_limits_enforced_client_side():
    with pytest.raises(ValueError):
        Choice(criteria={}).to_json()
    with pytest.raises(ValueError):
        Choice(criteria={str(i): "x" for i in range(256)}).to_json()
    with pytest.raises(ValueError):
        JevClient("")


# -- routing ---------------------------------------------------------------------

@pytest.mark.parametrize("said, needs, intent", [
    ("where is the export button", True, "point"),
    ("what's the capital of France", False, "answer"),
    ("explain this error", True, "explain"),
    ("thanks!", False, "chat"),
    ("why won't my code compile", True, "explain"),
    ("summarize it", True, "explain"),
])
def test_rule_route(said, needs, intent):
    route = rule_route(said)
    assert (route.needs_screen, route.intent, route.provider) == (needs, intent, "rules")


SCREENS = [ScreenInfo(1, Rect(0, 0, 100, 100), is_cursor_screen=True), ScreenInfo(2, Rect(100, 0, 100, 100))]


def jev_answers(needs=0.1, intent="answer", depth="quick", scope="cursor"):
    def choice(label, options, p=0.9):
        rest = (1 - p) / (len(options) - 1)
        return {"type": "choice", "choice": label, "confidence": p,
                "probabilities": {o: (p if o == label else rest) for o in options}}
    return {"answers": {"needs_screen": {"type": "noul", "noul": needs},
                        "intent": choice(intent, list(INTENTS)),
                        "depth": choice(depth, ["quick", "detailed"]),
                        "scope": choice(scope, ["cursor", "all"])}}


def test_jev_router_maps_typed_answers_to_route():
    jev, rec = client((200, jev_answers(needs=0.1, intent="answer", depth="detailed", scope="cursor")))
    route = asyncio.run(JevRouter(jev).route("explain how vaccines work", SCREENS))
    assert route.provider == "jev" and not route.needs_screen and route.detailed
    assert route.cursor_screen_only and route.intent == "answer"
    assert rec.calls[0][2]["state"] == {"user_said": "explain how vaccines work", "monitors": 2}
    assert set(rec.calls[0][2]["questions"]) == {"needs_screen", "intent", "depth", "scope", "task"}


def test_jev_router_keeps_screen_for_point_intent_and_skips_scope_on_one_monitor():
    jev, rec = client((200, jev_answers(needs=0.05, intent="point")))
    route = asyncio.run(JevRouter(jev).route("where's save", SCREENS[:1]))
    assert route.needs_screen and not route.cursor_screen_only
    assert "scope" not in rec.calls[0][2]["questions"]


def test_jev_router_falls_back_to_rules_on_failure():
    jev, _ = client((401, {"detail": "bad key"}))
    route = asyncio.run(JevRouter(jev).route("where is the export button", SCREENS))
    assert route.provider == "rules (jev unavailable)" and route.intent == "point"


def test_rule_router_is_async_compatible():
    assert asyncio.run(RuleRouter().route("hello", [])).intent == "chat"


# -- snapping --------------------------------------------------------------------

ELEMENTS = [
    Element("1", "button", "Export", Rect(500, 300, 80, 30)),
    Element("2", "button", "Import", Rect(600, 300, 80, 30)),
    Element("3", "group", "Toolbar", Rect(0, 280, 3000, 80)),
]


def test_heuristic_snap_moves_to_matching_element_center():
    snapper = ElementSnapper(lambda x, y, r: ELEMENTS)
    snapped = asyncio.run(snapper.snap(Target(x=530, y=322, label="Export button", screen=1)))
    assert (snapped.x, snapped.y, snapped.source) == (540, 315, "snapped")
    assert snapped.element == Rect(500, 300, 80, 30)


def test_snap_keeps_model_point_when_nothing_matches_or_no_label():
    snapper = ElementSnapper(lambda x, y, r: ELEMENTS)
    target = Target(x=530, y=322, label="Settings gear", screen=1)
    assert asyncio.run(snapper.snap(target)) == target
    unlabeled = Target(x=530, y=322, label="", screen=1)
    assert asyncio.run(snapper.snap(unlabeled)) == unlabeled


def test_huge_container_elements_are_never_snap_targets():
    snapper = ElementSnapper(lambda x, y, r: [ELEMENTS[2]], HeuristicChooser(threshold=0.1))
    target = Target(x=530, y=322, label="Toolbar", screen=1)
    assert asyncio.run(snapper.snap(target)) == target


def test_jev_chooser_picks_by_id_and_respects_none():
    def answer(choice):
        options = ["e0", "e1", "none"]
        return (200, {"answers": {"element": {"type": "choice", "choice": choice, "confidence": 0.9,
                                              "probabilities": {o: (0.9 if o == choice else 0.05) for o in options}}}})

    target = Target(x=530, y=322, label="Export button", screen=1)
    jev, rec = client(answer("e0"), answer("none"))
    chooser = JevChooser(jev)
    assert asyncio.run(chooser.choose("Export button", target, ELEMENTS[:2])) is ELEMENTS[0]
    sent = rec.calls[0][2]
    assert set(sent["questions"]["element"]["criteria"]) == {"e0", "e1", "none"}
    assert sent["state"]["pointing_at"] == "Export button"
    assert asyncio.run(chooser.choose("Export button", target, ELEMENTS[:2])) is None


def test_jev_chooser_falls_back_to_heuristics_when_unavailable():
    jev, _ = client(ConnectionResetError(), ConnectionResetError())
    target = Target(x=530, y=322, label="Export", screen=1)
    assert asyncio.run(JevChooser(jev).choose("Export", target, ELEMENTS[:2])) is ELEMENTS[0]


# -- speech out ------------------------------------------------------------------

def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_queue_speaker_plays_in_order_and_drains():
    said = []
    speaker = QueueSpeaker(PrintVoice(write=said.append))
    for text in ("one", "two", " ", "three"):
        speaker.speak(text)
    asyncio.run(asyncio.wait_for(speaker.drain(), 2))
    assert said == ["one", "two", "three"] and not speaker.busy


def test_queue_speaker_stop_cuts_current_and_drops_queue():
    started, played = threading.Event(), []

    class SlowVoice:
        name = "slow"

        def prepare(self, text):
            return text

        def play(self, prepared, stop):
            started.set()
            if not stop.wait(5):
                played.append(prepared)

    speaker = QueueSpeaker(SlowVoice())
    speaker.speak("long")
    speaker.speak("never")
    assert started.wait(2)
    speaker.stop()
    assert wait_until(lambda: not speaker.busy)
    assert played == []


def test_queue_speaker_uses_fallback_voice_when_primary_fails():
    said = []

    class Broken:
        name = "broken"

        def prepare(self, text):
            raise RuntimeError("ElevenLabs HTTP 401")

        def play(self, prepared, stop):  # pragma: no cover
            raise AssertionError

    speaker = QueueSpeaker(Broken(), fallback=PrintVoice(write=said.append))
    speaker.speak("hello")
    asyncio.run(asyncio.wait_for(speaker.drain(), 2))
    assert said == ["hello"] and "401" in speaker.errors[0]


def test_elevenlabs_request_shape():
    voice = ElevenLabsVoice(api_key="xi", voice_id="voice123", player="afplay")
    request = voice.request("Hello there.")
    assert request.full_url == ("https://api.elevenlabs.io/v1/text-to-speech/voice123/stream"
                                "?output_format=mp3_44100_128")
    assert request.get_header("Xi-api-key") == "xi"
    body = json.loads(request.data)
    assert body["text"] == "Hello there." and body["model_id"] == "eleven_flash_v2_5"


def test_a_walkthrough_always_looks_and_rules_think_harder_about_it_too():
    # live Jev said "no screen" to these; walkthroughs must still look
    for said in ("walk me through turning on two factor in github", "step by step, how do i make a new branch"):
        jev, _ = client((200, jev_answers(needs=0.2, intent="answer", depth="detailed")))
        route = asyncio.run(JevRouter(jev).route(said, SCREENS[:1]))
        assert route.needs_screen and route.detailed
    jev, _ = client((200, jev_answers(needs=0.1, intent="answer", depth="detailed")))
    assert not asyncio.run(JevRouter(jev).route("explain how black holes form", SCREENS[:1])).needs_screen
    # without a key, rules pick the deeper think too
    deep = ["walk me through turning on two factor in github", "how do i export this as a pdf",
            "help me set up a pivot table", "why isn't this code compiling", "explain how black holes form"]
    quick = ["where's the save button", "what's the capital of france", "thanks plip", "how many ounces in a cup",
             "summarize this page", "set a 10 minute timer"]
    assert [rule_route(said).detailed for said in deep] == [True] * len(deep)
    assert [rule_route(said).detailed for said in quick] == [False] * len(quick)

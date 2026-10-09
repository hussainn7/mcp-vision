"""Free path: Gemini via a Google AI Studio key, no plan, no install."""
from __future__ import annotations

import asyncio
import io
import json
import urllib.error

from buddy_fakes import shot
from mcp_vision.buddy.brain_gemini import GeminiError, GeminiKeyBrain, check_key
from mcp_vision.buddy.conversation import Turn


class Response:
    """HTTP response read line by line (Google's SSE)."""

    def __init__(self, chunks=(), body=b""):
        self.lines = [f"data: {json.dumps(chunk)}\r\n".encode() for chunk in chunks]
        self.body = body

    def __iter__(self):
        for line in self.lines:
            yield line
            yield b"\r\n"

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, message, quota=""):
    error = {"message": message}
    if quota:
        error["details"] = [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota}]}]
    return urllib.error.HTTPError("https://g", code, "err", {}, io.BytesIO(json.dumps({"error": error}).encode()))


class Google:
    """Answers each request in turn; remembers what was sent."""

    def __init__(self, *answers):
        self.answers, self.sent = list(answers), []

    def __call__(self, request, timeout=None):
        self.sent.append((request.full_url, json.loads(request.data) if request.data else None, dict(request.headers)))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def text(words, **extra):
    return {"candidates": [{"content": {"parts": [{"text": words}]}, **extra}]}


def run(brain, turns, **kw):
    async def collect():
        return [piece async for piece in brain.stream(system="be plip", turns=turns, **kw)]
    return "".join(asyncio.run(collect()))


def test_a_turn_streams_with_the_screenshot_and_reports_what_it_used():
    google = Google(Response([text("It's under "), {"candidates": [{"content": {"parts": [
        {"text": "thinking about menus", "thought": True}, {"text": "File."}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 1200, "candidatesTokenCount": 8, "thoughtsTokenCount": 40,
                          "cachedContentTokenCount": 200}, "modelVersion": "gemini-3.8-flash"}]))
    brain = GeminiKeyBrain(api_key="AIza-test", opener=google)
    turns = [Turn("user", "hi"), Turn("assistant", "hey"), Turn("user", "where is export", images=[shot()])]
    assert run(brain, turns) == "It's under File."                                # thought parts aren't said
    url, body, headers = google.sent[0]
    assert url.endswith("/models/gemini-flash-latest:streamGenerateContent?alt=sse")
    assert headers["X-goog-api-key"] == "AIza-test" and "AIza" not in url          # key never in the URL
    assert body["systemInstruction"] == {"parts": [{"text": "be plip"}]}
    assert [content["role"] for content in body["contents"]] == ["user", "model", "user"]
    assert "inlineData" in body["contents"][2]["parts"][0] and body["contents"][2]["parts"][-1] == {"text": "where is export"}
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    used = brain.last_usage
    assert (used.input, used.cache_read, used.output, used.model) == (1000, 200, 48, "gemini-3.8-flash")


def test_a_detailed_ask_thinks_harder_and_turns_from_one_side_merge():
    brain = GeminiKeyBrain(api_key="k", effort="medium")
    body = brain.request(system="s", turns=[Turn("assistant", "earlier"), Turn("user", "a"), Turn("user", "b")],
                         detailed=True)
    assert body["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "high"
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]      # user first, then alternates
    assert body["contents"][2]["parts"] == [{"text": "a"}, {"text": "b"}]


def test_a_model_that_wont_take_a_thinking_level_is_asked_again_without_one():
    google = Google(http_error(400, "Thinking level is not supported for this model."), Response([text("ok")]))
    assert run(GeminiKeyBrain(api_key="k", opener=google), [Turn("user", "hi")]) == "ok"
    assert "thinkingConfig" not in google.sent[1][1]["generationConfig"]


def test_a_bad_key_and_the_free_limit_are_said_plainly():
    from mcp_vision.buddy.companion import _friendly_error

    quota = "Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests"
    for args, said in (((400, "API key not valid. Please pass a valid API key."), "Check the API key"),
                       ((429, quota, "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"), "Give it a minute"),
                       ((429, quota, "GenerateRequestsPerDayPerProjectPerModel-FreeTier"), "resets tomorrow")):
        try:                                               # (the lite model is tried too, and is out as well)
            run(GeminiKeyBrain(api_key="k", opener=Google(http_error(*args), http_error(*args))), [Turn("user", "hi")])
        except GeminiError as exc:
            assert said in _friendly_error(exc)
        else:
            raise AssertionError("no error")


def test_long_and_blocked_answers_end_in_words_not_silence():
    long = Google(Response([text("Step one.", finishReason="MAX_TOKENS")]))
    assert run(GeminiKeyBrain(api_key="k", opener=long), [Turn("user", "hi")]).endswith("Ask me to keep going.")
    blocked = Google(Response([{"promptFeedback": {"blockReason": "SAFETY"}}]))
    assert "can't help with that one" in run(GeminiKeyBrain(api_key="k", opener=blocked), [Turn("user", "hi")])


def test_checking_a_key_says_what_to_do():
    assert check_key("AIza-good", opener=Google(Response(body=b'{"models": []}'))) == ""
    assert "didn't accept that key" in check_key("AIza-bad", opener=Google(http_error(400, "API key not valid.")))
    assert "internet" in check_key("AIza-x", opener=Google(OSError("offline")))


def test_a_pasted_google_key_is_checked_first_then_saved_and_becomes_the_brain(tmp_path):
    from mcp_vision.buddy.settings import BuddySettings
    from mcp_vision.buddy.settings_service import Platform, SettingsService
    from mcp_vision.buddy.store import History, Prefs

    key = "AIzaSyD-" + "x" * 31
    posted, checked = [], []
    clipboard = {"text": "my grocery list"}
    service = SettingsService(
        engines=lambda: [{"id": "claude-code", "status": "logged-out"}, {"id": "gemini-api", "status": "ready"}],
        settings=lambda: BuddySettings(_env_file=None), reload=lambda: None, post=posted.extend,
        prefs_path=tmp_path / "prefs.json", env_path=tmp_path / ".env", history=History(path=tmp_path / "h.jsonl"),
        platform=Platform(clipboard=lambda: clipboard["text"]),
        check_key=lambda name, value: checked.append((name, value)) or ("" if value == key else "nope"),
        background=lambda job: job())
    service.handle({"cmd": "paste-key"})                                          # no key copied yet
    state = posted[-1]["state"]["keyCheck"]
    assert state["state"] == "bad" and "no key on your clipboard" in state["message"] and checked == []
    assert "grocery" not in json.dumps(posted)                                    # other clipboard text isn't kept
    clipboard["text"] = f"  {key}\n"
    service.handle({"cmd": "paste-key"})
    assert checked == [("GEMINI_API_KEY", key)] and f"GEMINI_API_KEY={key}" in (tmp_path / ".env").read_text()
    assert posted[-1]["state"]["keyCheck"]["state"] == "ok"
    assert Prefs.load(tmp_path / "prefs.json").engine == "gemini-api"             # their only working brain
    service.handle({"cmd": "set-key", "name": "GEMINI_API_KEY", "value": "AIza" + "y" * 35})
    assert posted[-1]["state"]["keyCheck"] == {"name": "GEMINI_API_KEY", "state": "bad", "message": "nope"}
    assert "y" * 35 not in (tmp_path / ".env").read_text()                         # rejected key isn't saved


def test_the_free_engine_is_ready_with_a_key_and_builds_a_gemini_brain():
    from mcp_vision.buddy.engines import BY_ID, choose_engine, make_engine_brain, probe
    from mcp_vision.buddy.settings import BuddySettings

    settings = BuddySettings(_env_file=None, GEMINI_API_KEY="AIza-k")
    status = probe(BY_ID["gemini-api"], settings)
    assert status.status == "ready"
    brain = make_engine_brain(status, settings)
    assert isinstance(brain, GeminiKeyBrain) and brain.api_key == "AIza-k" and brain.model == "gemini-flash-latest"
    assert probe(BY_ID["gemini-api"], BuddySettings(_env_file=None)).status == "missing-key"
    assert choose_engine(settings, [status]).spec.id == "gemini-api"


def test_new_aq_keys_paste_and_cli_errors_keep_the_error_line(tmp_path):
    from mcp_vision.buddy.engines import _tail
    from mcp_vision.buddy.settings import BuddySettings
    from mcp_vision.buddy.settings_service import KEY_SHAPES, Platform, SettingsService
    from mcp_vision.buddy.store import History

    key = "AQ.Ab8RN6" + "x" * 60                                  # AI Studio's keys since May 2026
    assert KEY_SHAPES[0][1].fullmatch(key)
    checked = []
    service = SettingsService(engines=lambda: [], settings=lambda: BuddySettings(_env_file=None), reload=lambda: None,
                              post=lambda m: None, prefs_path=tmp_path / "p.json", env_path=tmp_path / ".env",
                              history=History(path=tmp_path / "h.jsonl"), platform=Platform(clipboard=lambda: key),
                              check_key=lambda name, value: checked.append(name) or "", background=lambda job: job())
    service.handle({"cmd": "paste-key"})
    assert checked == ["GEMINI_API_KEY"] and key in (tmp_path / ".env").read_text()
    stderr = ("Warning: 256-color support not detected.\nError authenticating: IneligibleTierError: This client is no longer "
              "supported for Gemini Code Assist for individuals.\n    at throwIneligibleOrProjectIdError (chunk.js:1:1)\n"
              "    at process.processTicksAndRejections (node:internal)")
    said = _tail(stderr)
    assert said.startswith("Error authenticating: IneligibleTierError") and " at " not in said


def test_an_overloaded_gemini_is_tried_again_then_the_lite_model_answers(monkeypatch):
    import mcp_vision.buddy.brain_gemini as gemini
    from mcp_vision.buddy.companion import _friendly_error

    busy = "This model is currently experiencing high demand. Please try again later."
    google = Google(http_error(503, busy), Response([text("Hi there.")]))
    assert run(GeminiKeyBrain(api_key="k", opener=google), [Turn("user", "hi")]) == "Hi there."
    assert [url.split("/models/")[1].split(":")[0] for url, _, _ in google.sent] == \
        ["gemini-flash-latest", "gemini-flash-lite-latest"]                   # straight to the lite model
    google = Google(*[http_error(503, busy)] * 2)
    try:
        run(GeminiKeyBrain(api_key="k", opener=google), [Turn("user", "hi")])
    except GeminiError as exc:
        assert "overloaded right now" in _friendly_error(exc)                # not "something went wrong"
    else:
        raise AssertionError("no error")
    google = Google(http_error(400, "API key not valid."), Response([text("never")]))
    try:
        run(GeminiKeyBrain(api_key="k", opener=google), [Turn("user", "hi")])
    except GeminiError:
        assert len(google.sent) == 1                                         # a bad key isn't retried


def test_a_gemini_that_wont_start_talking_hands_over_to_the_lite_model(monkeypatch):
    import threading

    import mcp_vision.buddy.brain_gemini as gemini

    monkeypatch.setattr(gemini, "FIRST_WORD", 0.2)
    hung = threading.Event()

    class Hangs(Response):
        def __iter__(self):
            hung.wait(2)                                   # no bytes, like a queued request
            return iter(())
    google = Google(Hangs(), Response([text("Hi.")]))
    assert run(GeminiKeyBrain(api_key="k", opener=google), [Turn("user", "hi")]) == "Hi."
    hung.set()
    assert google.sent[1][0].split("/models/")[1].startswith("gemini-flash-lite-latest")


def test_after_flash_was_busy_the_next_asks_go_straight_to_lite_for_a_while():
    busy = "This model is currently experiencing high demand."
    google = Google(http_error(503, busy), Response([text("One.")]), Response([text("Two.")]))
    brain = GeminiKeyBrain(api_key="k", opener=google)
    assert run(brain, [Turn("user", "hi")]) == "One."
    assert run(brain, [Turn("user", "again")]) == "Two."
    models = [url.split("/models/")[1].split(":")[0] for url, _, _ in google.sent]
    assert models == ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-flash-lite-latest"]

"""The free way in: Gemini with a Google AI Studio key, no AI plan and nothing to install."""
from __future__ import annotations

import asyncio
import io
import json
import urllib.error

from buddy_fakes import shot
from mcp_vision.buddy.brain_gemini import GeminiError, GeminiKeyBrain, check_key
from mcp_vision.buddy.conversation import Turn


class Response:
    """An HTTP response read line by line, like Google's server-sent events."""

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


def http_error(code, message):
    return urllib.error.HTTPError("https://g", code, "err", {}, io.BytesIO(json.dumps({"error": {"message": message}}).encode()))


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
    assert run(brain, turns) == "It's under File."                                # the thought part isn't said
    url, body, headers = google.sent[0]
    assert url.endswith("/models/gemini-flash-latest:streamGenerateContent?alt=sse")
    assert headers["X-goog-api-key"] == "AIza-test" and "AIza" not in url          # the key never rides in the link
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
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]      # starts with the user, alternates
    assert body["contents"][2]["parts"] == [{"text": "a"}, {"text": "b"}]


def test_a_model_that_wont_take_a_thinking_level_is_asked_again_without_one():
    google = Google(http_error(400, "Thinking level is not supported for this model."), Response([text("ok")]))
    assert run(GeminiKeyBrain(api_key="k", opener=google), [Turn("user", "hi")]) == "ok"
    assert "thinkingConfig" not in google.sent[1][1]["generationConfig"]


def test_a_bad_key_and_the_free_limit_are_said_plainly():
    from mcp_vision.buddy.companion import _friendly_error

    for error, said in ((http_error(400, "API key not valid. Please pass a valid API key."), "Check the API key"),
                        (http_error(429, "Quota exceeded for metric: generativelanguage.googleapis.com/"
                                         "generate_content_free_tier_requests, limit: 250"), "free AI limit")):
        try:
            run(GeminiKeyBrain(api_key="k", opener=Google(error)), [Turn("user", "hi")])
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
    service.handle({"cmd": "paste-key"})                                          # nothing key-like copied yet
    state = posted[-1]["state"]["keyCheck"]
    assert state["state"] == "bad" and "no key on your clipboard" in state["message"] and checked == []
    assert "grocery" not in json.dumps(posted)                                    # what else was copied isn't kept
    clipboard["text"] = f"  {key}\n"
    service.handle({"cmd": "paste-key"})
    assert checked == [("GEMINI_API_KEY", key)] and f"GEMINI_API_KEY={key}" in (tmp_path / ".env").read_text()
    assert posted[-1]["state"]["keyCheck"]["state"] == "ok"
    assert Prefs.load(tmp_path / "prefs.json").engine == "gemini-api"             # their only working brain
    service.handle({"cmd": "set-key", "name": "GEMINI_API_KEY", "value": "AIza" + "y" * 35})
    assert posted[-1]["state"]["keyCheck"] == {"name": "GEMINI_API_KEY", "state": "bad", "message": "nope"}
    assert "y" * 35 not in (tmp_path / ".env").read_text()                         # a rejected key isn't saved


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

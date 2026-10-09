"""Subscription engines: discovery, sign-in probes, choosing a brain, and streaming through real subprocesses.

Each CLI is replaced by a tiny fake executable that checks the flags Plip
passes and prints the JSON lines the real tool prints, so the asyncio
subprocess path, parsing, cancellation, and error mapping all run for real.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import textwrap
import time

import pytest

from mcp_vision.buddy.companion import Companion, _friendly_error
from mcp_vision.buddy.conversation import Turn
from mcp_vision.buddy.engines import (
    BY_ID, SPECS, ClaudeCodeBrain, ClaudeCodeParser, CodexBrain, CodexParser, CursorBrain, CursorParser,
    AntigravityBrain, EngineError, EngineRegistry, EngineStatus, RunResult, choose_engine,
    find_binary, make_engine_brain, probe, transcript_prompt,
)
from mcp_vision.buddy.geometry import Rect, ScreenInfo, Screenshot
from mcp_vision.buddy.prompt import TEXT_ONLY_NOTE
from mcp_vision.buddy.settings import BuddySettings


def shot(index=1, width=1280, height=800):
    screen = ScreenInfo(index=index, frame=Rect(0, 0, 1512, 982), scale=2.0, is_cursor_screen=index == 1)
    return Screenshot(screen=screen, data=b"\xff\xd8fake-jpeg", width=width, height=height)


def fake_cli(tmp_path, name, body):
    """An executable ``name`` running ``body`` (Python) with ``argv``/``stdin_text`` in scope."""
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\nimport json, os, sys\nargv = sys.argv[1:]\n"
                    "stdin_text = '' if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()\n"
                    "def out(obj):\n    print(json.dumps(obj), flush=True)\n" + textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


async def collect(brain, turns, **kw):
    return "".join([text async for text in brain.stream(system="SYSTEM", turns=turns, **kw)])


TURNS = [Turn("user", "where is wifi"), Turn("assistant", "Top right. [POINT:1200,10:wifi:screen1]"),
         Turn("user", "the user said: and bluetooth?", images=(shot(),))]


# -- discovery and probes ------------------------------------------------------------

def test_specs_cover_the_subscriptions_people_have():
    assert [spec.id for spec in SPECS] == ["claude-code", "codex", "cursor", "antigravity", "anthropic", "gemini-api"]
    assert BY_ID["gemini-api"].kind == "api" and BY_ID["gemini-api"].key_name == "GEMINI_API_KEY"   # free, no plan
    assert {spec.label for spec in SPECS if spec.kind == "subscription"} == {"Claude", "ChatGPT", "Cursor", "Gemini"}
    assert BY_ID["cursor"].vision is False and BY_ID["claude-code"].vision is True


def test_find_binary_searches_path_then_install_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    local = tmp_path / ".local" / "bin"
    local.mkdir(parents=True)
    tool = local / "claude"
    tool.write_text("#!/bin/sh\n")
    assert find_binary(["claude"], path="") is None                       # not executable yet
    tool.chmod(0o755)
    assert find_binary(["claude"], path="") == str(tool)
    first = tmp_path / "first"
    first.mkdir()
    (first / "claude").write_text("#!/bin/sh\n")
    (first / "claude").chmod(0o755)
    assert find_binary(["claude"], path=str(first)) == str(first / "claude")
    assert find_binary(["cursor-agent", "agent"], path="") is None
    generic = tmp_path / "generic"
    generic.mkdir()
    (generic / "agent").write_text("#!/bin/sh\n")
    (generic / "agent").chmod(0o755)
    assert find_binary(["agent"], path=str(generic)) is None              # some other tool called agent
    cursor_home = tmp_path / ".local" / "share" / "cursor-agent" / "versions" / "1"
    cursor_home.mkdir(parents=True)
    (cursor_home / "cursor-agent").write_text("#!/bin/sh\n")
    (cursor_home / "cursor-agent").chmod(0o755)
    (local / "agent").symlink_to(cursor_home / "cursor-agent")
    assert find_binary(["agent"], path="") == str(local / "agent")


def runner_for(table):
    calls = []

    def run(argv, timeout):
        calls.append(argv[1:])
        return table.get(tuple(argv[1:]), RunResult(1, "", "unknown command"))
    run.calls = calls
    return run


def test_probe_claude_code_reads_auth_status(tmp_path):
    settings = BuddySettings(_env_file=None)
    which = lambda names: "/opt/homebrew/bin/claude"    # noqa: E731
    signed_in = runner_for({("auth", "status", "--json"): RunResult(
        0, json.dumps({"loggedIn": True, "email": "h@example.com", "subscriptionType": "max"}), "")})
    status = probe(BY_ID["claude-code"], settings, runner=signed_in, which=which, home=tmp_path)
    assert status.status == "ready" and status.detail == "Signed in as h@example.com · max"
    token = runner_for({("auth", "status", "--json"): RunResult(
        0, json.dumps({"loggedIn": True, "authMethod": "oauth_token", "apiProvider": "firstParty"}), "")})
    assert probe(BY_ID["claude-code"], settings, runner=token, which=which, home=tmp_path).detail == \
        "Signed in · Claude plan"
    signed_out = runner_for({("auth", "status", "--json"): RunResult(1, json.dumps({"loggedIn": False}), "")})
    status = probe(BY_ID["claude-code"], settings, runner=signed_out, which=which, home=tmp_path)
    assert status.status == "logged-out"
    assert status.card()["login"] == "/opt/homebrew/bin/claude auth login"


def test_probe_codex_cursor_gemini(tmp_path):
    settings = BuddySettings(_env_file=None)
    which = lambda names: f"/usr/local/bin/{names[0]}"    # noqa: E731
    codex = probe(BY_ID["codex"], settings, which=which, home=tmp_path, runner=runner_for(
        {("login", "status"): RunResult(0, "", "Logged in using ChatGPT\n")}))
    assert codex.status == "ready" and codex.detail == "Signed in with ChatGPT"
    codex_out = probe(BY_ID["codex"], settings, which=which, home=tmp_path, runner=runner_for(
        {("login", "status"): RunResult(1, "Not logged in\n", "")}))
    assert codex_out.status == "logged-out"
    cursor = probe(BY_ID["cursor"], settings, which=which, home=tmp_path, runner=runner_for(
        {("status", "--format", "json"): RunResult(0, json.dumps({"isAuthenticated": True,
                                                                  "email": "h@example.com"}), "")}))
    assert cursor.status == "ready" and cursor.detail == "Signed in as h@example.com"
    cursor_text = probe(BY_ID["cursor"], settings, which=which, home=tmp_path, runner=runner_for(
        {("status", "--format", "json"): RunResult(0, "\n ✓ Logged in as h@example.com\n", "")}))
    assert cursor_text.status == "ready" and cursor_text.detail == "Logged in as h@example.com"
    cursor_out = probe(BY_ID["cursor"], settings, which=which, home=tmp_path, runner=runner_for(
        {("status", "--format", "json"): RunResult(1, "Not logged in", "")}))
    assert cursor_out.status == "logged-out" and cursor_out.card()["login"] == "/usr/local/bin/cursor-agent login"


def test_probe_missing_cli_and_api_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    nothing = lambda names: None    # noqa: E731
    missing = probe(BY_ID["codex"], BuddySettings(_env_file=None), which=nothing)
    assert missing.status == "not-installed" and missing.card()["install"] == "npm i -g @openai/codex"
    assert probe(BY_ID["anthropic"], BuddySettings(_env_file=None)).status == "missing-key"
    keyed = probe(BY_ID["anthropic"], BuddySettings(_env_file=None, anthropic_api_key="sk-ant"))
    assert keyed.status == "ready" and keyed.card()["keyName"] == "ANTHROPIC_API_KEY"


def statuses(**states):
    return [EngineStatus(spec, states.get(spec.id.replace("-", "_"), "not-installed"), path=f"/bin/{spec.id}")
            for spec in SPECS]


def test_choose_engine_prefers_the_users_pick_then_subscriptions():
    settings = BuddySettings(_env_file=None)
    assert choose_engine(settings, statuses()) is None
    assert choose_engine(settings, statuses(codex="ready", anthropic="ready")).spec.id == "anthropic"
    assert choose_engine(settings, statuses(codex="ready", claude_code="ready")).spec.id == "claude-code"
    assert choose_engine(settings, statuses(cursor="unknown")).spec.id == "cursor"
    picked = BuddySettings(_env_file=None, engine="codex")
    assert choose_engine(picked, statuses(codex="logged-out", claude_code="ready")).spec.id == "codex"
    assert choose_engine(picked, statuses(claude_code="ready")).spec.id == "claude-code"   # codex not installed
    assert choose_engine(settings, statuses(gemini="ready", cursor="ready"), preferred="cursor").spec.id == "cursor"


def test_registry_caches_and_only_probes_when_asked():
    calls = []
    registry = EngineRegistry(lambda: BuddySettings(_env_file=None, anthropic_api_key="sk"),
                              runner=lambda argv, timeout: calls.append(argv) or RunResult(1, "", ""),
                              which=lambda names: None)
    cards = registry.cards()
    assert calls == [] and {card["status"] for card in cards} == {"unknown"}
    cards = registry.cards(probe=True)
    by_id = {card["id"]: card for card in cards}
    assert by_id["anthropic"]["status"] == "ready" and by_id["anthropic"]["selected"] is True
    assert by_id["claude-code"]["status"] == "not-installed" and "install" in by_id["claude-code"]
    assert registry.cards()[0]["status"] == "not-installed"            # cached, no new probes


def test_make_engine_brain_builds_the_right_class():
    settings = BuddySettings(_env_file=None, cli_model="sonnet", effort="medium")
    brain = make_engine_brain(EngineStatus(BY_ID["claude-code"], "ready", path="/x/claude"), settings)
    assert isinstance(brain, ClaudeCodeBrain) and brain.binary == "/x/claude" and brain.model == "sonnet"
    assert brain.kind == "subscription" and brain.label == "Claude"
    assert isinstance(make_engine_brain(EngineStatus(BY_ID["cursor"], "ready", path="/x/a"), settings), CursorBrain)


# -- prompts -------------------------------------------------------------------------------

def test_transcript_prompt_carries_history_screens_and_system():
    text = transcript_prompt(TURNS, ["/tmp/screen1.jpg"], system="BE PLIP")
    assert text.index("BE PLIP") < text.index("<earlier_conversation>") < text.index("<screenshots>")
    assert "user: where is wifi" in text and "you (plip): Top right." in text
    assert "/tmp/screen1.jpg: the user's screen (cursor is here) (image dimensions: 1280x800 pixels)" in text
    assert text.endswith("the user said: and bluetooth?")
    assert transcript_prompt([Turn("user", "hi")]) == "hi"


# -- parsers -------------------------------------------------------------------------------

def feed(parser, events):
    out = []
    for event in events:
        out += parser.feed(json.dumps(event) if isinstance(event, dict) else event)
    return out + parser.finish()


def test_claude_code_parser_streams_deltas_and_reports_errors():
    parser = ClaudeCodeParser()
    events = [{"type": "system", "subtype": "init"},
              {"type": "stream_event", "event": {"type": "content_block_delta",
                                                 "delta": {"type": "text_delta", "text": "Click "}}},
              {"type": "stream_event", "event": {"type": "content_block_delta",
                                                 "delta": {"type": "thinking_delta", "thinking": "hmm"}}},
              {"type": "stream_event", "event": {"type": "content_block_delta",
                                                 "delta": {"type": "text_delta", "text": "Wi-Fi."}}},
              {"type": "assistant", "message": {"content": [{"type": "text", "text": "Click Wi-Fi."}]}},
              {"type": "result", "subtype": "success", "is_error": False, "result": "Click Wi-Fi."}, "not json"]
    assert feed(parser, events) == ["Click ", "Wi-Fi."]
    failed = ClaudeCodeParser()
    assert feed(failed, [{"type": "result", "subtype": "success", "is_error": True,
                          "result": "Invalid API key · Please run /login"}]) == []
    assert "Please run /login" in failed.error
    whole = ClaudeCodeParser()
    assert feed(whole, [{"type": "assistant", "message": {"content": [{"type": "text", "text": "Hi."}]}},
                        {"type": "result", "subtype": "success", "result": "Hi."}]) == ["Hi."]


def test_a_cli_retry_mid_reply_doesnt_run_the_reply_twice():
    def delta(text):
        return {"type": "stream_event", "event": {"type": "content_block_delta",
                                                  "delta": {"type": "text_delta", "text": text}}}
    start = {"type": "stream_event", "event": {"type": "message_start", "message": {}}}
    parser = ClaudeCodeParser()
    texts = feed(parser, [start, delta("Typing it. "), delta('[DO:type_text {"text": "hi"}]'),
                          start, delta("Typing it. "), delta('[DO:type_text {"text": "hi"}]'),
                          {"type": "result", "subtype": "success", "result": "Typing it."}])
    assert texts == ["Typing it. ", '[DO:type_text {"text": "hi"}]'] and parser.retried
    fresh = ClaudeCodeParser()                # the first message_start of a reply is normal
    assert feed(fresh, [start, delta("Hi.")]) == ["Hi."] and not fresh.retried


def test_codex_parser_emits_message_text_once():
    parser = CodexParser()
    events = [{"type": "thread.started", "thread_id": "t"}, {"type": "turn.started"},
              {"type": "item.completed", "item": {"id": "item_0", "type": "reasoning", "text": "thinking"}},
              {"type": "item.updated", "item": {"id": "item_1", "type": "agent_message", "text": "Open the"}},
              {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": "Open the menu."}},
              {"type": "turn.completed", "usage": {}}]
    assert feed(parser, events) == ["Open the", " menu."]
    failed = CodexParser()
    feed(failed, [{"type": "turn.failed", "error": {"message": "You've hit your usage limit."}}])
    assert failed.error == "You've hit your usage limit."


def cursor_assistant(text, **extra):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
            **extra}


def test_cursor_parser_keeps_deltas_and_skips_flushes():
    parser = CursorParser()
    events = [{"type": "system", "subtype": "init"},
              cursor_assistant("It's ", timestamp_ms=1), cursor_assistant("in ", timestamp_ms=2),
              cursor_assistant("It's in ", timestamp_ms=3, model_call_id="m1"),       # flush before a tool call
              cursor_assistant("Settings.", timestamp_ms=4),
              cursor_assistant("It's in Settings."),                                   # end-of-turn flush
              {"type": "result", "subtype": "success", "result": "It's in Settings."}]
    assert feed(parser, events) == ["It's ", "in ", "Settings."]
    whole = CursorParser()                               # no partial output: fall back to the result
    assert feed(whole, [cursor_assistant("Hi."), {"type": "result", "subtype": "success", "result": "Hi."}]) == ["Hi."]


# -- real subprocesses ---------------------------------------------------------------------------

def test_claude_code_brain_streams_images_through_stdin(tmp_path):
    binary = fake_cli(tmp_path, "claude", """
        assert argv[0] == "-p" and "--include-partial-messages" in argv
        assert argv[argv.index("--system-prompt") + 1] == "SYSTEM"
        assert argv[argv.index("--tools") + 1] == ""
        assert "--safe-mode" in argv and "--no-session-persistence" in argv
        assert argv[argv.index("--effort") + 1] == "low"
        assert "CLAUDECODE" not in os.environ
        assert os.environ["CLAUDE_CODE_PROMPT_CACHE_TTL"] == "5m"      # never re-read: no hour-long cache writes
        assert os.environ["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"    # no telemetry flush before exit
        message = json.loads(stdin_text.strip().splitlines()[0])
        content = message["message"]["content"]
        # history first, a block per message, the newest marked for the cache; then the screen, then the ask
        assert content[0]["text"].startswith("<earlier_conversation>") and "where is wifi" in content[0]["text"]
        assert "cache_control" not in content[0] and content[1]["cache_control"] == {"type": "ephemeral"}
        assert content[2]["type"] == "image" and content[2]["source"]["media_type"] == "image/jpeg"
        assert "image dimensions: 1280x800" in content[3]["text"]
        assert content[-1]["text"].startswith("</earlier_conversation>")
        assert content[-1]["text"].endswith("and bluetooth?")
        assert not os.listdir(".")                                   # runs in an empty scratch dir
        for word in ["Bluetooth ", "is ", "next to Wi-Fi. ", "[POINT:1180,12:bluetooth:screen1]"]:
            out({"type": "stream_event", "event": {"type": "content_block_delta",
                                                   "delta": {"type": "text_delta", "text": word}}})
        out({"type": "result", "subtype": "success", "is_error": False, "result": "done"})
    """)
    text = asyncio.run(collect(ClaudeCodeBrain(binary), TURNS))
    assert text == "Bluetooth is next to Wi-Fi. [POINT:1180,12:bluetooth:screen1]"


def test_each_turns_history_starts_with_the_last_turns_so_the_cache_can_give_it_back():
    from mcp_vision.buddy.engines import history_blocks

    first = history_blocks(TURNS[:2])
    later = history_blocks([*TURNS[:2], Turn("user", "and bluetooth?"), Turn("assistant", "Next to it.")])
    unmarked = [{key: value for key, value in block.items() if key != "cache_control"} for block in later[:2]]
    assert unmarked == [{key: value for key, value in block.items() if key != "cache_control"} for block in first]
    assert [("cache_control" in block) for block in later] == [False, False, False, True]
    assert history_blocks([]) == []


def test_history_drops_a_few_exchanges_at_once_so_its_start_holds_still():
    from mcp_vision.buddy.conversation import Conversation

    talk = Conversation(max_turns=20)
    starts = []
    for n in range(30):
        talk.record(f"ask {n}", f"answer {n}")
        starts.append(talk.turns[0].text)
    assert len(set(starts)) <= 8 and talk.turns[0].role == "user" and len(talk.turns) <= 20   # was 21: a new start each turn


def test_codex_brain_attaches_screens_and_reads_stdin(tmp_path):
    binary = fake_cli(tmp_path, "codex", """
        assert argv[:2] == ["exec", "--json"] and argv[-2:] == ["--", "-"] and "--ephemeral" in argv
        image = argv[argv.index("--image") + 1]
        assert os.path.exists(image) and open(image, "rb").read().startswith(b"\\xff\\xd8")
        assert "SYSTEM" in stdin_text and image in stdin_text
        assert 'model_reasoning_effort="medium"' in argv                  # detailed steps low -> medium
        out({"type": "item.completed", "item": {"id": "1", "type": "agent_message", "text": "Open Control Center."}})
        out({"type": "turn.completed"})
    """)
    assert asyncio.run(collect(CodexBrain(binary, effort="low"), TURNS, detailed=True)) == "Open Control Center."


def test_cursor_brain_is_text_only(tmp_path):
    binary = fake_cli(tmp_path, "cursor-agent", """
        assert argv[0] == "-p" and "--stream-partial-output" in argv
        assert argv[argv.index("--mode") + 1] == "ask" and os.path.realpath(argv[argv.index("--workspace") + 1]) == os.path.realpath(os.getcwd())
        assert "SYSTEM" in argv[-1] and "and bluetooth?" in argv[-1]
        out({"type": "assistant", "timestamp_ms": 1, "message": {"content": [{"type": "text", "text": "Top "}]}})
        out({"type": "assistant", "timestamp_ms": 2, "message": {"content": [{"type": "text", "text": "right."}]}})
        out({"type": "assistant", "message": {"content": [{"type": "text", "text": "Top right."}]}})
        out({"type": "result", "subtype": "success", "result": "Top right."})
    """)
    brain = CursorBrain(binary)
    assert brain.vision is False
    assert asyncio.run(collect(brain, TURNS)) == "Top right."


def test_antigravity_runs_sandboxed_text_only_at_the_asked_depth(tmp_path):
    binary = fake_cli(tmp_path, "agy", """
        assert argv[argv.index("-p") + 1].startswith("<instructions>") and "--sandbox" in argv
        assert argv[argv.index("--model") + 1] == "gemini-3.8-flash-high"
        out({"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "Bluetooth's up top."}})
        out({"event": "result", "result": {"status": "SUCCESS", "response": "Bluetooth's up top."}})
    """)
    brain = AntigravityBrain(binary, effort="high")
    assert brain.vision is False and asyncio.run(collect(brain, TURNS)) == "Bluetooth's up top."


def test_cli_errors_become_friendly_messages(tmp_path):
    binary = fake_cli(tmp_path, "claude", """
        out({"type": "result", "subtype": "success", "is_error": True, "result": "Not logged in · Please run /login"})
        sys.exit(1)
    """)
    with pytest.raises(EngineError) as caught:
        asyncio.run(collect(ClaudeCodeBrain(binary), TURNS))
    assert "Please run /login" in str(caught.value)
    assert _friendly_error(caught.value).startswith("I need you to sign in")

    crash = fake_cli(tmp_path, "codex", """
        sys.stderr.write("thread 'main' panicked\\nError: 429 Too Many Requests\\n")
        sys.exit(2)
    """)
    with pytest.raises(EngineError) as caught:
        asyncio.run(collect(CodexBrain(crash), TURNS))
    assert "429" in str(caught.value) and "rate limited" in _friendly_error(caught.value)

    with pytest.raises(EngineError):
        asyncio.run(collect(CursorBrain(str(tmp_path / "missing")), TURNS))

    silent = fake_cli(tmp_path, "agy", """
        out({"event": "init"})
    """)
    with pytest.raises(EngineError, match="returned no answer"):
        asyncio.run(collect(AntigravityBrain(silent), TURNS))


def test_cli_timeout_and_cancellation_kill_the_process(tmp_path):
    marker = tmp_path / "pid"
    binary = fake_cli(tmp_path, "slow", f"""
        import time
        open({str(marker)!r}, "w").write(str(os.getpid()))
        out({{"type": "item.completed", "item": {{"id": "1", "type": "agent_message", "text": "Hold on."}}}})
        time.sleep(30)
    """)
    brain = CodexBrain(binary, timeout=0.6)
    with pytest.raises(EngineError, match="timed out"):
        asyncio.run(collect(brain, TURNS))
    pid = int(marker.read_text())

    def alive(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        # A zombie still answers kill(0); check its state.
        try:
            with open(f"/proc/{pid}/stat") as handle:
                return handle.read().split()[2] != "Z"
        except OSError:
            return False
    assert not alive(pid)

    async def cancel_midway():
        brain = CodexBrain(binary, timeout=30)
        stream = brain.stream(system="S", turns=TURNS)
        first = await stream.__anext__()
        await stream.aclose()
        return first
    assert asyncio.run(cancel_midway()) == "Hold on."
    assert not alive(int(marker.read_text()))


def test_companion_runs_a_text_only_engine_on_the_screen_map(tmp_path):
    """Cursor can't see images: the companion sends the controls list instead of screenshots."""
    seen = tmp_path / "prompt.txt"
    binary = fake_cli(tmp_path, "cursor-agent", f"""
        open({str(seen)!r}, "w").write(argv[-1])
        out({{"type": "assistant", "timestamp_ms": 1, "message": {{"content": [{{"type": "text", "text": "Hit Export. [POINT:400,300:Export:screen1]"}}]}}}})
        out({{"type": "result", "subtype": "success"}})
    """)

    class Capturer:
        def screens(self):
            return [shot().screen]

        def capture(self, *, only_cursor_screen=False):
            return [shot()]

    class Context:
        def snapshot(self):
            from mcp_vision.buddy.screen_context import Control, ScreenContext

            return ScreenContext(app="Keynote", window="Deck", controls=[
                Control(label="Export", role="AXButton", x=472.5, y=368.25)])

    class Pointer:
        def __init__(self):
            self.points = []

        def set_state(self, state, detail=""):
            pass

        def point(self, x, y, label):
            self.points.append(label)

        def release(self):
            pass

    pointer = Pointer()
    companion = Companion(brain=CursorBrain(binary), capturer=Capturer(), pointer=pointer, context=Context(),
                          walkthroughs=False)
    result = asyncio.run(companion.respond("where is export"))
    prompt = seen.read_text()
    assert result.state == "done" and pointer.points == ["Export"]
    assert "frontmost app: Keynote" in prompt and "Export | AXButton | 400,300" in prompt
    assert "<screenshots>" not in prompt and TEXT_ONLY_NOTE in prompt, prompt[-1500:]


def test_no_first_answer_in_time_names_the_network_problem(tmp_path):
    binary = fake_cli(tmp_path, "codex", """
        import time
        out({"type": "thread.started", "thread_id": "t"})
        out({"type": "error", "message": "Reconnecting... waiting for network (Connection failed: error sending request)"})
        time.sleep(30)
    """)
    with pytest.raises(EngineError) as caught:
        asyncio.run(collect(CodexBrain(binary, first_output_timeout=0.8), TURNS))
    assert "Reconnecting" in str(caught.value)
    assert _friendly_error(caught.value).startswith("I can't reach my brain")


def test_the_turn_ends_at_the_answer_not_when_the_cli_gets_round_to_exiting(tmp_path):
    binary = fake_cli(tmp_path, "claude", """
        import time
        out({"type": "stream_event", "event": {"type": "content_block_delta",
                                               "delta": {"type": "text_delta", "text": "Top right."}}})
        out({"type": "result", "subtype": "success", "is_error": False, "result": "Top right.",
             "usage": {"input_tokens": 10, "output_tokens": 3}})
        time.sleep(30)                    # still flushing its own traffic, stdout open
    """)
    brain = ClaudeCodeBrain(binary)
    started = time.monotonic()
    assert asyncio.run(collect(brain, TURNS)) == "Top right."
    assert time.monotonic() - started < 2.5 and brain.last_usage.output == 3


# -- what each brain says it used ----------------------------------------------------------

def test_claude_code_reports_tokens_cost_and_the_model_that_answered(tmp_path):
    binary = fake_cli(tmp_path, "claude", """
        out({"type": "stream_event", "event": {"type": "content_block_delta",
                                               "delta": {"type": "text_delta", "text": "Hi."}}})
        out({"type": "result", "subtype": "success", "is_error": False, "result": "Hi.", "total_cost_usd": 0.0123,
             "usage": {"input_tokens": 12, "output_tokens": 30, "cache_read_input_tokens": 4000,
                       "cache_creation_input_tokens": 900},
             "modelUsage": {"claude-opus-5-5[1m]": {"canonicalModel": "claude-opus-5-5[1m]", "inputTokens": 12}}})
    """)
    brain = ClaudeCodeBrain(binary)
    assert asyncio.run(collect(brain, TURNS)) == "Hi."
    used = brain.last_usage
    assert (used.input, used.output, used.cache_read, used.cache_write) == (12, 30, 4000, 900)
    assert used.model == "claude-opus-5-5" and used.cost == 0.0123 and not used.estimated


def test_codex_cursor_and_gemini_report_their_own_spellings(tmp_path):
    codex = fake_cli(tmp_path, "codex", """
        out({"type": "item.completed", "item": {"id": "1", "type": "agent_message", "text": "Yes."}})
        out({"type": "turn.completed", "usage": {"input_tokens": 1000, "cached_input_tokens": 600, "output_tokens": 9}})
        out({"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 1}})
    """)
    brain = CodexBrain(codex, model="gpt-5")
    asyncio.run(collect(brain, TURNS))
    assert (brain.last_usage.input, brain.last_usage.cache_read, brain.last_usage.output) == (500, 600, 10)
    assert brain.last_usage.model == "gpt-5"                      # the CLI didn't say; the brain's own setting

    cursor = fake_cli(tmp_path, "cursor-agent", """
        out({"type": "assistant", "timestamp_ms": 1, "message": {"content": [{"type": "text", "text": "Ok."}]}})
        out({"type": "result", "subtype": "success", "result": "Ok.", "model": "sonnet-5",
             "usage": {"inputTokens": 50, "outputTokens": 4, "cacheReadTokens": 20, "cacheWriteTokens": 2}})
    """)
    brain = CursorBrain(cursor)
    asyncio.run(collect(brain, TURNS))
    assert (brain.last_usage.input, brain.last_usage.cache_write, brain.last_usage.model) == (50, 2, "sonnet-5")

    agy = fake_cli(tmp_path, "agy", """
        out({"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "Sure."}})
        out({"event": "result", "result": {"status": "SUCCESS", "response": "Sure.",
             "usage": {"input_tokens": 700, "output_tokens": 5, "cache_read_tokens": 200}}})
    """)
    brain = AntigravityBrain(agy)
    asyncio.run(collect(brain, TURNS))
    assert (brain.last_usage.input, brain.last_usage.cache_read, brain.last_usage.output) == (500, 200, 5)
    assert brain.last_usage.model == "gemini-3.8-flash-low"


def test_a_brain_that_reports_nothing_leaves_no_usage(tmp_path):
    binary = fake_cli(tmp_path, "claude", """
        out({"type": "result", "subtype": "success", "is_error": False, "result": "Plain."})
    """)
    brain = ClaudeCodeBrain(binary)
    brain.last_usage = "left over from the last call"
    assert asyncio.run(collect(brain, TURNS)) == "Plain."
    assert brain.last_usage is None


def test_the_api_brain_reports_the_final_messages_usage():
    from types import SimpleNamespace

    from mcp_vision.buddy.brain_claude import ClaudeBrain

    final = SimpleNamespace(stop_reason="end_turn", model="claude-opus-5-5",
                            usage=SimpleNamespace(input_tokens=20, output_tokens=7, cache_read_input_tokens=3000,
                                                  cache_creation_input_tokens=None))

    class Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def __aiter__(self):
            async def events():
                yield SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="Hi."))
            return events()

        async def get_final_message(self):
            return final

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=lambda **body: Stream())))
    brain = ClaudeBrain(client=client)
    assert asyncio.run(collect(brain, [Turn("user", "hi")])) == "Hi."
    used = brain.last_usage
    assert (used.input, used.output, used.cache_read, used.cache_write, used.model) == (20, 7, 3000, 0, "claude-opus-5-5")


# -- a warm claude process for the next turn -------------------------------------------------

def test_the_app_keeps_the_next_claude_process_warm_and_reuses_it(tmp_path):
    marker = tmp_path / "pids"
    binary = fake_cli(tmp_path, "claude", f"""
        open({str(marker)!r}, "a").write(str(os.getpid()) + "\\n")
        out({{"type": "stream_event", "event": {{"type": "content_block_delta",
                                                "delta": {{"type": "text_delta", "text": "ok"}}}}}})
        out({{"type": "result", "subtype": "success", "is_error": False, "result": "ok"}})
    """)

    async def three_turns():
        brain = ClaudeCodeBrain(binary)
        brain.prewarm = True
        answers = [await collect(brain, TURNS)]
        for _ in range(50):                                   # next process starts in the background
            if brain._spares:
                break
            await asyncio.sleep(0.02)
        warm_pid = next(iter(brain._spares.values()))[0].pid
        answers.append(await collect(brain, TURNS))           # same command line: uses the warm one
        await asyncio.sleep(0.2)
        answers.append(await collect(brain, TURNS, detailed=True))   # different effort: a fresh process
        await brain.aclose()
        return answers, warm_pid

    answers, warm_pid = asyncio.run(three_turns())
    pids = [int(line) for line in marker.read_text().split()]
    assert answers == ["ok", "ok", "ok"] and pids[1] == warm_pid


def test_the_companion_warms_a_brain_that_keeps_spares_and_skips_one_that_doesnt():
    from buddy_fakes import Capturer, ScriptedBrain

    asked = []

    class Spare(ScriptedBrain):
        prewarm = True

        def ensure_warm(self, system, effort=None):
            asked.append((system[:10], effort))

    Companion(brain=Spare(), capturer=Capturer()).warm_brain()
    Companion(brain=ScriptedBrain(), capturer=Capturer()).warm_brain()       # nothing to warm: no error
    off = Spare()
    off.prewarm = False                                                       # one-shot runs: never
    Companion(brain=off, capturer=Capturer()).warm_brain()
    assert asked == [("you're pli", None)]


def test_one_shot_runs_dont_leave_a_process_waiting(tmp_path):
    binary = fake_cli(tmp_path, "claude", """
        out({"type": "result", "subtype": "success", "is_error": False, "result": "ok"})
    """)
    brain = ClaudeCodeBrain(binary)
    assert asyncio.run(collect(brain, TURNS)) == "ok" and not brain._spares


def test_a_process_starts_while_the_keys_are_held_and_an_unused_one_goes_away(tmp_path):
    marker = tmp_path / "pids"
    binary = fake_cli(tmp_path, "claude", f"""
        open({str(marker)!r}, "a").write(str(os.getpid()) + "\\n")
        out({{"type": "result", "subtype": "success", "is_error": False, "result": "ok"}})
    """)

    async def press_then_ask():
        brain = ClaudeCodeBrain(binary, effort="medium")
        brain.prewarm, brain.idle_ttl = True, 0.6
        brain.ensure_warm("SYSTEM")                          # key press: nothing to say yet
        brain.ensure_warm("SYSTEM")                          # second press: no extra process
        for _ in range(100):
            if brain._spares:
                break
            await asyncio.sleep(0.02)
        spare_pid = next(iter(brain._spares.values()))[0].pid
        answer = await collect(brain, TURNS)                 # question arrives: uses the spare
        brain.ensure_warm("SYSTEM", effort="low")            # low-effort spare for a check-in
        await asyncio.sleep(0.3)
        efforts = sorted(key[0][key[0].index("--effort") + 1] for key in brain._spares)
        await asyncio.sleep(1.0)                             # unused: reaped
        left = dict(brain._spares)
        await brain.aclose()
        return answer, spare_pid, efforts, left

    answer, spare_pid, efforts, left = asyncio.run(press_then_ask())
    pids = [int(line) for line in marker.read_text().split()]
    assert answer == "ok" and pids[0] == spare_pid and efforts == ["low", "medium"] and left == {}


def test_a_brain_that_starts_running_a_command_is_stopped_before_anything_it_read_comes_back(tmp_path):
    from mcp_vision.buddy.companion import _friendly_error

    codex = fake_cli(tmp_path, "codex", """
        out({"type": "item.started", "item": {"id": "c1", "type": "command_execution", "command": "cat ~/.ssh/id_rsa"}})
        out({"type": "item.completed", "item": {"id": "m1", "type": "agent_message",
                                                "text": "here it is: -----BEGIN KEY----- [DO:open_url {\\"url\\": \\"x\\"}]"}})
    """)
    agy = fake_cli(tmp_path, "agy", """
        out({"event": "step_update", "step_update": {"step_type": "tool", "tool_name": "view_file"}})
        out({"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "the file says ..."}})
    """)
    said = []

    async def run(brain):
        async for text in brain.stream(system="SYSTEM", turns=TURNS):
            said.append(text)
    for brain in (CodexBrain(codex), AntigravityBrain(agy)):
        with pytest.raises(EngineError) as stopped:
            asyncio.run(run(brain))
        assert "tried to run a command" in str(stopped.value)
        assert _friendly_error(stopped.value).startswith("My brain tried to run a command on your Mac")
    assert said == []                                                  # nothing it read was spoken or acted on


def test_gemini_on_a_google_plan_goes_through_antigravity(tmp_path, monkeypatch):
    from mcp_vision.buddy.engines import AntigravityParser, choose_engine

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    settings = BuddySettings(_env_file=None)
    (tmp_path / ".gemini").mkdir()
    assert probe(BY_ID["antigravity"], settings, which=lambda names: "/x/agy", home=tmp_path).status == "logged-out"
    (tmp_path / ".gemini" / "jetski-standalone-oauth-token").write_text("t")
    agy = probe(BY_ID["antigravity"], settings, which=lambda names: "/x/agy", home=tmp_path)
    assert agy.status == "ready" and choose_engine(settings, [agy], preferred="gemini").spec.id == "antigravity"   # an old pick
    parser = AntigravityParser()
    parser.model = "gemini-3.8-flash-medium"
    lines = ['{"event":"init","init":{"tools":["run_command"]}}',
             '{"event":"step_update","step_update":{"step_type":"agent_response","state":"ACTIVE","text_delta":"hello"}}',
             '{"event":"result","result":{"status":"SUCCESS","response":"hello\\n","usage":{"input_tokens":100,'
             '"output_tokens":5,"cache_read_tokens":40}}}']
    assert [text for line in lines for text in parser.feed(line)] == ["hello"] and parser.ended
    assert (parser.usage.input, parser.usage.cache_read, parser.usage.output) == (60, 40, 5)
    tool = AntigravityParser()
    tool.model = ""
    tool.feed('{"event":"step_update","step_update":{"step_type":"tool","state":"ACTIVE","tool_name":"view_file"}}')
    assert tool.error and tool.ended                                      # stopped before it reads anything back

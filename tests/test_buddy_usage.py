"""Token counts and what they'd cost at API prices (buddy/usage.py)."""
from __future__ import annotations

import pytest

from mcp_vision.buddy.usage import Usage, estimate, from_report, image_tokens, rates_for, text_tokens


def test_every_brains_spelling_normalizes_to_the_same_counts():
    anthropic = from_report({"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 900,
                             "cache_creation_input_tokens": 50}, model="claude-opus-5-5")
    assert (anthropic.input, anthropic.output, anthropic.cache_read, anthropic.cache_write) == (100, 20, 900, 50)
    assert anthropic.tokens_in == 1050
    # Codex / Gemini count cached tokens inside the input total
    codex = from_report({"input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 30}, cached_in_input=True)
    assert (codex.input, codex.cache_read, codex.output) == (200, 800, 30)
    cursor = from_report({"inputTokens": 40, "outputTokens": 5, "cacheReadTokens": 7, "cacheWriteTokens": 3})
    assert (cursor.input, cursor.output, cursor.cache_read, cursor.cache_write) == (40, 5, 7, 3)
    gemini = from_report({"input": 500, "cached": 100, "candidates_tokens": 10, "thoughts": 15}, cached_in_input=True)
    assert (gemini.input, gemini.cache_read, gemini.output) == (400, 100, 25)   # thinking counts as output
    assert from_report({"total_tokens": 77}).input == 77
    assert from_report({}) is None and from_report(None) is None and from_report({"input_tokens": 0}) is None


def test_price_uses_the_brains_own_cost_first_then_the_table():
    assert Usage(input=1000, cost=0.42).price() == 0.42                   # Claude Code says what it cost
    used = Usage(input=1_000_000, output=1_000_000, cache_read=1_000_000, cache_write=1_000_000,
                 model="claude-opus-5-5")
    rate_in, rate_out, rate_read = rates_for("claude-opus-5-5")
    assert used.price() == pytest.approx(rate_in + rate_out + rate_read + rate_in * 1.25)   # writes at 1.25x input
    assert rates_for("claude-opus-5-5-20261001") == rates_for("claude-opus-5-5")         # specific id wins
    assert Usage(input=1_000_000).price("codex") == pytest.approx(rates_for("gpt-5")[0])  # brain's usual model
    assert Usage(input=1_000_000, model="some-local-model").price() == 0.0


def test_a_silent_brain_is_estimated_at_four_characters_a_token():
    guess = estimate(1200, "x" * 400, model="gemini-2.5-pro")
    assert (guess.input, guess.output, guess.estimated) == (1200, 100, True)
    assert estimate(10, "").output == 0
    assert text_tokens("abcd" * 10) == 10 and text_tokens("abcde") == 2
    assert image_tokens(1280, 800) == 1365 and image_tokens(4000, 3000) == 1600


def test_adding_turns_keeps_the_cost_and_the_estimate_flag():
    total = Usage(input=10, cost=0.1) + Usage(output=5, estimated=True, model="sonnet")
    assert (total.input, total.output, total.cost, total.estimated, total.model) == (10, 5, 0.1, True, "sonnet")
    assert (Usage(input=1) + Usage(input=2)).cost is None


# -- one row per request ---------------------------------------------------------------------

import asyncio  # noqa: E402
import json  # noqa: E402

from buddy_fakes import Capturer, Events, FakeHost, Pointer, ScriptedBrain, Speaker  # noqa: E402
from mcp_vision.buddy.actions import ActionContext, ActionEngine  # noqa: E402
from mcp_vision.buddy.actions.host import FileHit  # noqa: E402
from mcp_vision.buddy.companion import Companion  # noqa: E402
from mcp_vision.buddy.usage import OUTCOMES, UsageLog  # noqa: E402


def buddy(brain, log, host=None, **kw):
    actions = ActionEngine(ActionContext(host=host or FakeHost(), state={}))
    return Companion(brain=brain, capturer=Capturer(), speaker=Speaker(), pointer=Pointer(), observer=Events(),
                     actions=actions, walkthroughs=False, usage=log, **kw)


def test_a_question_is_one_row_with_an_estimate_when_the_brain_says_nothing(tmp_path):
    log = UsageLog(tmp_path / "usage.jsonl")
    result = asyncio.run(buddy(ScriptedBrain("It's top right."), log).respond("where's wifi"))
    assert result.outcome == "answered" and result.usage.estimated
    (row,) = log.rows()
    assert set(row) == {"at", "engine", "label", "kind", "model", "input", "output", "cache_read", "cache_write",
                        "cost", "estimated", "turns", "actions", "outcome", "goal", "ms"}
    assert (row["engine"], row["label"], row["kind"], row["outcome"]) == ("scripted", "Scripted", "voice", "answered")
    assert row["turns"] == 1 and row["goal"] is False and row["estimated"] is True
    assert row["input"] > 1365 and row["output"] == len("It's top right.") // 4   # the screenshot counts


def test_a_task_logs_every_model_call_and_the_action_names(tmp_path):
    log = UsageLog(tmp_path / "usage.jsonl")
    host = FakeHost(home=str(tmp_path))
    host.files = [FileHit(str(tmp_path / "Documents/Invoice.pdf"), 1759370000.0)]

    class Counting(ScriptedBrain):
        async def stream(self, **kw):
            self.last_usage = None
            async for text in super().stream(**kw):
                yield text
            self.last_usage = Usage(input=100, output=10, cache_read=1000, model="claude-opus-5-5", cost=0.01)

    brain = Counting('Looking. [DO:search_files {"query": "invoice"}]', 'Found it. [DO:open_file {"index": 1}]')
    result = asyncio.run(buddy(brain, log, host).respond("open my invoice"))
    assert result.outcome == "done" and result.turns == 2
    (row,) = log.rows()
    assert row["actions"] == ["search_files", "open_file"] and row["goal"] is True and row["turns"] == 2
    assert (row["input"], row["output"], row["cache_read"], row["cost"]) == (200, 20, 2000, 0.02)
    assert row["model"] == "claude-opus-5-5" and row["estimated"] is False


def test_how_it_ended_failed_waiting_and_stopped(tmp_path):
    log = UsageLog(tmp_path / "usage.jsonl")

    class Broken(ScriptedBrain):
        async def stream(self, **kw):
            raise RuntimeError("Claude timed out.")
            yield ""

    assert asyncio.run(buddy(Broken(), log).respond("hello")).outcome == "failed"
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    (desktop / "Screenshot 1.png").write_bytes(b"x")
    waiting = Companion(brain=ScriptedBrain("I can tidy that. [DO:organize_desktop {}]"), capturer=Capturer(),
                        speaker=Speaker(), pointer=Pointer(), walkthroughs=False, usage=log,
                        actions=ActionEngine(ActionContext(host=FakeHost(), state={"desktop": str(desktop)})))
    assert asyncio.run(waiting.respond("tidy my desktop")).outcome == "waiting"

    class Slow(ScriptedBrain):
        async def stream(self, **kw):
            yield "Let me think "
            await asyncio.sleep(10)
            yield "about it."

    async def stop_it():
        plip = buddy(Slow(), log)
        task = asyncio.ensure_future(plip.respond("explain quantum physics"))
        await asyncio.sleep(0.2)
        plip.interrupt()
        return await task

    assert asyncio.run(stop_it()).outcome == "stopped"
    rows = log.rows()
    assert [row["outcome"] for row in rows] == ["failed", "waiting", "stopped"]
    assert rows[0]["turns"] == 1 and rows[0]["input"] == 0             # asked, failed, no tokens known
    assert all(row["outcome"] in OUTCOMES for row in rows)


def test_the_log_survives_junk_and_clears(tmp_path):
    path = tmp_path / "usage.jsonl"
    path.write_text('{"at": 1, "outcome": "done"}\nnot json\n["a list"]\n{"no": "time"}\n')
    log = UsageLog(path)
    assert log.rows() == [{"at": 1, "outcome": "done"}]
    log.clear()
    assert log.rows() == [] and not path.exists()
    assert json.loads(json.dumps(log.rows())) == []

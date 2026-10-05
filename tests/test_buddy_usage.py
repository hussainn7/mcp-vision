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

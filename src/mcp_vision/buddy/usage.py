"""What Plip's requests cost, kept on this Mac.

Every brain reports token counts at the end of a call (Claude Code also reports
what that call would cost at API prices). ``Usage`` normalizes those reports;
when a brain says nothing, the companion estimates from what it sent and got
back and marks the record ``estimated``.

``UsageLog`` appends one line per request (a question, or a whole multi-step
task) to ``usage.jsonl`` in the state folder.

Dollar figures are what the traffic would cost at pay-per-use API rates. On a
Claude, ChatGPT, Cursor or Google plan nothing is billed per token; the number
is there to show what the plan is worth.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from mcp_vision.paths import state_dir

# $ per million tokens: input, output, cache read. Cache writes cost 1.25x input.
# First match wins, so specific ids come before families.
PRICES: tuple[tuple[str, tuple[float, float, float]], ...] = (
    ("claude-fable-5", (10.0, 50.0, 0.25)),
    ("claude-mythos-5", (10.0, 50.0, 0.25)),
    ("claude-opus-5-5", (4.0, 20.0, 0.20)),
    ("claude-opus-5", (5.0, 25.0, 0.50)),
    ("claude-opus-4", (5.0, 25.0, 0.50)),
    ("claude-sonnet-5", (2.0, 10.0, 0.20)),
    ("claude-sonnet-4", (3.0, 15.0, 0.30)),
    ("claude-haiku", (1.0, 5.0, 0.10)),
    ("opus", (4.0, 20.0, 0.20)),
    ("sonnet", (2.0, 10.0, 0.20)),
    ("haiku", (1.0, 5.0, 0.10)),
    ("claude", (2.0, 10.0, 0.20)),
    # Other plans' models, at their public API list prices (approximate).
    ("gpt-5-mini", (0.25, 2.0, 0.025)),
    ("gpt", (1.25, 10.0, 0.125)),
    ("codex", (1.25, 10.0, 0.125)),
    ("o3", (2.0, 8.0, 0.5)),
    ("o4", (1.1, 4.4, 0.275)),
    ("gemini-2.5-flash", (0.30, 2.50, 0.075)),
    ("gemini", (1.25, 10.0, 0.31)),
)
# What each brain usually runs when it doesn't say which model answered.
DEFAULT_PRICES = {"claude-code": "claude-sonnet-5", "claude": "claude-sonnet-5", "codex": "gpt-5",
                  "cursor": "claude-sonnet-5", "gemini": "gemini-2.5-pro"}


@dataclass
class Usage:
    input: int = 0              # uncached input tokens
    output: int = 0             # output tokens, thinking included
    cache_read: int = 0
    cache_write: int = 0
    model: str = ""
    cost: float | None = None   # what the brain itself says this cost at list price (Claude Code does)
    estimated: bool = False

    @property
    def tokens_in(self) -> int:
        return self.input + self.cache_read + self.cache_write

    def __add__(self, other: Usage) -> Usage:
        cost = None if self.cost is None and other.cost is None else (self.cost or 0.0) + (other.cost or 0.0)
        return Usage(self.input + other.input, self.output + other.output, self.cache_read + other.cache_read,
                     self.cache_write + other.cache_write, other.model or self.model, cost,
                     self.estimated or other.estimated)

    def price(self, engine: str = "") -> float:
        """Dollars at API list price: the brain's own figure when it gave one, else from the price table."""
        if self.cost is not None:
            return self.cost
        rates = rates_for(self.model or DEFAULT_PRICES.get(engine, ""))
        if rates is None:
            return 0.0
        rate_in, rate_out, rate_read = rates
        return (self.input * rate_in + self.cache_write * rate_in * 1.25 + self.cache_read * rate_read
                + self.output * rate_out) / 1_000_000


def rates_for(model: str) -> tuple[float, float, float] | None:
    lowered = model.lower()
    for prefix, rates in PRICES:
        if prefix in lowered:
            return rates
    return None


def _int(raw: dict, *keys: str) -> int:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    return 0


def from_report(raw: dict[str, Any] | None, *, model: str = "", cost: float | None = None,
                cached_in_input: bool = False) -> Usage | None:
    """Normalize a usage block from any brain (Anthropic, Claude Code, Codex, Cursor, Gemini spellings).

    ``cached_in_input``: OpenAI-style counts, where the input total already includes cached tokens.
    """
    if not isinstance(raw, dict) or not raw:
        return None
    usage = Usage(
        input=_int(raw, "input_tokens", "inputTokens", "prompt_tokens", "promptTokens", "input"),
        output=_int(raw, "output_tokens", "outputTokens", "completion_tokens", "completionTokens", "output",
                    "candidates_tokens"),
        cache_read=_int(raw, "cache_read_input_tokens", "cacheReadInputTokens", "cached_input_tokens",
                        "cacheReadTokens", "cached", "cached_tokens"),
        cache_write=_int(raw, "cache_creation_input_tokens", "cacheCreationInputTokens", "cacheWriteTokens"),
        model=model, cost=cost)
    if cached_in_input:
        usage.input = max(0, usage.input - usage.cache_read)
    usage.output += _int(raw, "thoughts", "thoughts_tokens", "thoughtsTokens")     # Gemini counts thinking apart
    if not (usage.input or usage.output or usage.cache_read or usage.cache_write):
        total = _int(raw, "total_tokens", "totalTokens")
        if not total:
            return None
        usage.input = total
    return usage


def estimate(input_tokens: int, reply: str, model: str = "") -> Usage:
    """What a brain that reports nothing probably used: what Plip sent, and its reply at ~4 characters a token."""
    return Usage(input=max(0, int(input_tokens)), output=max(1, len(reply) // 4) if reply else 0, model=model,
                 estimated=True)


def text_tokens(text: str) -> int:
    return (len(text) + 3) // 4


def image_tokens(width: int, height: int) -> int:
    """Anthropic's published estimate for an image: width x height / 750, about 1600 at most after resizing."""
    return min(1600, max(1, round(width * height / 750)))


# -- the log -----------------------------------------------------------------------------------

OUTCOMES = ("done", "answered", "unverified", "paused", "waiting", "failed", "stopped")


@dataclass
class Request:
    """One thing the user asked for, start to finish (every model call and action it took)."""

    at: float
    engine: str = ""            # engine id: claude-code, codex, cursor, gemini, claude (the API)
    label: str = ""             # what the UI calls it: Claude, ChatGPT…
    kind: str = "voice"         # voice | cli
    model: str = ""
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost: float = 0.0           # at API list price
    estimated: bool = False
    turns: int = 0              # model calls
    actions: list[str] = field(default_factory=list)      # names only, never what they were about
    outcome: str = "answered"   # one of OUTCOMES
    goal: bool = False          # a multi-step task (more than one model call)
    ms: int = 0


class UsageLog:
    def __init__(self, path: Path | None = None, keep: int = 5000):
        self.path = path or state_dir() / "usage.jsonl"
        self.keep = keep

    def add(self, request: Request) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(request), separators=(",", ":")) + "\n")
            if self.path.stat().st_size > self.keep * 600:
                rows = self.rows()
                self.path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n"
                                             for row in rows[-self.keep:]), encoding="utf-8")
        except OSError:
            pass                        # usage is nice to have; never break a turn over it

    def rows(self) -> list[dict[str, Any]]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("at"), (int, float)):
                out.append(row)
        return out

    def clear(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass


__all__ = ["OUTCOMES", "PRICES", "Request", "Usage", "UsageLog", "estimate", "from_report", "image_tokens",
           "rates_for", "text_tokens"]

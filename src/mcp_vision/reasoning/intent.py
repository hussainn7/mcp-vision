"""Intent understanding: raw words -> a compact, structured understanding.

The literal request and the underlying objective are kept distinct. Incomplete
prompts are expected and reasoned over. Entity/constraint extraction here is the
*fast path*; the model reasoner refines judgment on top of this seed. We never
pretend the heuristic is omniscient — it just gets the loop moving.
"""
from __future__ import annotations

import re
from datetime import date

from mcp_vision.reasoning.consequence import level_for_request
from mcp_vision.reasoning.schemas import AgentState, P_USER_ASSERTED

_POLITE = re.compile(
    r"^\s*(?:please|can you|could you|would you|hey|hi|yo|help me(?: to)?|"
    r"could you please|can we|imagine you'?re|pretend|"
    r"i(?:\s+(?:really|kinda|kind of))?\s+wanna(?:\s+go)?|"
    r"i'?d\s+like to|i\s+would\s+like to|i\s+want(?: to)?|"
    r"i'?m\s+(?:looking|trying)\s+to|let'?s|go(?: ahead)? and)\s*", re.I)
_PRICE = re.compile(r"\b(?:under|less than|around|about|at most|max|cheaper than|budget(?: of)?)\s*\$?(\d+(?:\.\d{1,2})?|\bk\b)", re.I)
_PREF_EXPENSIVE = re.compile(r"\b(best|premium|high-?end|fastest|top-?tier)\b", re.I)
_PREF_CHEAP = re.compile(r"\b(cheap|cheaper|cheapest|affordable|budget|inexpensive|decent)\b", re.I)
_PREF_FAST = re.compile(r"\b(fast|quick|short|sooner)\b", re.I)
_PREF_EASY = re.compile(r"\b(easy|simple|convenient|hassle-?free)\b", re.I)

_DATE_HINTS = (
    "today", "tomorrow", "this week", "next week", "this weekend", "next weekend",
    "this month", "next month", "anytime", "whenever", "flexible", "any date",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
)
_OBJECTIVE_STOP = re.compile(r"\b(but|however|and also)\b", re.I)


def _mode(request: str, text: str) -> str:
    """Light mode guess. The runtime adapter may override with the real one."""
    if re.search(r"\b(where do i|how do i|how to|show me|point to|highlight|which (button|setting|tab))\b", text):
        return "guide"
    if re.search(r"^\s*(?:what|why|which|is|are|does|should i|explain|summarize)\b", text):
        return "ask"
    if re.match(
        r"\s*(?:(?:please|can you|could you)\s+)*"
        r"(write|fill|apply|send|submit|book|buy|delete|move|create|open|click|change|organize|"
        r"clean|fix|diagnose|research|find|search|look up|look for|get)\b", text):
        return "act"
    return "ask"


def objective_of(raw: str) -> str:
    """A short, human objective phrase for the working state (not a plan)."""
    text = _POLITE.sub("", (raw or "").strip()).strip().rstrip(".,!?")
    # Cut trailing politeness / low-signal clauses.
    text = _OBJECTIVE_STOP.sub(".", text).split(".")[0].strip()
    return text or (raw or "").strip() or "unspecified goal"


def analyze(raw: str, context: object | None = None, *,
            today: date | None = None) -> AgentState:
    """Seed an AgentState with intent understanding. Pure and deterministic."""
    raw = (raw or "").strip()
    text = raw.lower()
    low = _POLITE.sub("", text)
    mode = _mode(raw, low)

    state = AgentState(raw_request=raw, objective=objective_of(raw), mode=mode)
    state.consequence_level = level_for_request(raw)

    # Explicit constraints & preferences worth tracking, cheaply.
    m = _PRICE.search(raw)
    if m:
        cap = m.group(1)
        state.constraints_explicit["price_cap"] = cap
    if any(word in text for word in _DATE_HINTS):
        state.constraints_explicit["timing"] = [w for w in _DATE_HINTS if w in text]
        state.constraints_inferred["timing_flexible"] = bool(re.search(r"\b(anytime|whenever|flexible|any )\b", text))
    if re.search(r"\b(flight|flights)\b", text):
        state.constraints_explicit["travel"] = True
    if _PREF_CHEAP.search(text):
        state.preferences["cost"] = "low"
    if _PREF_EXPENSIVE.search(text):
        state.preferences["cost"] = "high"
    if _PREF_FAST.search(text):
        state.preferences["speed"] = "fast"
    if _PREF_EASY.search(text):
        state.preferences["effort"] = "low"
    state.add_fact("objective", state.objective, source=P_USER_ASSERTED, confidence=1.0)
    state.add_fact("raw_request", raw, source=P_USER_ASSERTED, confidence=1.0)
    state.facts.sort(key=lambda f: 0 if f.note in {"raw_request"} else 1)
    return state
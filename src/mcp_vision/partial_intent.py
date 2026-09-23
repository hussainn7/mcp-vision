"""Safe preparation while speech is still streaming.

Partial transcripts are unreliable, so this module only returns *labels* and
idempotent warmup hints. It never performs a mutation; the final transcript
still routes through the normal task pipeline.
"""
from __future__ import annotations

import re
import time


def preparation_for(text: str) -> dict | None:
    """Return a safe preparation hint for a stable speech prefix, if any."""
    text = (text or "").strip()
    if not text:
        return None
    if re.search(r"\bflights?\b", text, re.I):
        return {"kind": "flights", "label": "Finding flights…"}
    if re.search(r"\b(search|research|look\s?up|google)\b", text, re.I):
        return {"kind": "search", "label": "Preparing search…"}
    from mcp_vision.native_apps import parse_intent
    intent = parse_intent(text)
    if intent is not None and intent.action == "open_app":
        return {"kind": "open_app", "label": f"Opening {intent.value}…", "app": intent.value}
    return None


class PartialIntentWatcher:
    """Fires once per preparation kind once the prefix is stable enough.

    Apple Speech partials revise aggressively, so a hint is only emitted after
    the same kind is seen twice with at least `min_gap` seconds of speech.
    """

    def __init__(self, min_gap: float = 0.4):
        self.min_gap = min_gap
        self.seen: dict[str, float] = {}
        self.fired: set[str] = set()

    def observe(self, text: str) -> dict | None:
        prep = preparation_for(text)
        if prep is None:
            return None
        kind = prep["kind"]
        now = time.monotonic()
        first = self.min_gap if kind == "flights" else self.min_gap
        if kind not in self.seen:
            self.seen[kind] = now
            return None
        if kind in self.fired or now - self.seen[kind] < first:
            return None
        self.fired.add(kind)
        return prep

    def reset(self) -> None:
        self.seen.clear()
        self.fired.clear()

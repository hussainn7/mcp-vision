"""Native (AppleScript/JS) snapshot must surface full rendered page text.

The pruned accessibility tree drops JS-rendered content like Google Flights
offers, which used to make the live-native ask loop report
"required answer evidence is missing". The snapshot now prefers
document.body.innerText when it is longer than the AX-derived text.
"""
from __future__ import annotations

import asyncio
import json
import types

from mcp_vision.native_browser import NativeBrowserRuntime


def _runtime(eval_fn):
    rt = NativeBrowserRuntime(pause_for_challenges=False)
    rt._current = types.SimpleNamespace(
        window=1, tab=1, url="https://www.google.com/travel/flights",
        title="Flights", key="tab1")
    # _run is awaited, so it must return an awaitable that calls the callable.
    async def _run(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    rt._run = _run
    rt._eval = eval_fn
    async def _noop():
        return None
    rt._ensure = _noop
    rt._check_url = lambda u: u
    return rt


def _eval_switcher(ax_text: str, inner_text: str):
    def ev(js: str) -> str:
        if js.startswith("JSON.stringify"):
            return json.dumps({
                "url": "https://www.google.com/travel/flights", "title": "Flights",
                "text": ax_text, "elements": [],
                "facts": [], "identity": {}, "pruned": {}})
        return inner_text
    return ev


def test_snapshot_prefers_rendered_inner_text_over_pruned_ax_text():
    ax_text = "ATL–SFO"
    inner_text = ("Frontier 5:25 PM – 7:48 PM 5 hr 23 min ATL–SFO Nonstop US$423 "
                  "round trip")
    rt = _runtime(_eval_switcher(ax_text, inner_text))
    snap = asyncio.run(rt.snapshot())
    # The longer, real offer text must win over the short AX excerpt.
    assert snap.text == inner_text
    assert "5:25 PM" in snap.text and "$423" in snap.text


def test_snapshot_keeps_ax_text_when_inner_text_is_shorter():
    ax_text = "Rich accessibility text that is already long enough."
    inner_text = "short"
    rt = _runtime(_eval_switcher(ax_text, inner_text))
    snap = asyncio.run(rt.snapshot())
    assert snap.text == ax_text

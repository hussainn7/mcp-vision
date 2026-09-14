"""Simple natural-language query against your existing Chrome."""
from __future__ import annotations

import asyncio
import re
from urllib.parse import quote_plus

from mcp_vision.core.governor import Governor
from mcp_vision.summarize import summarize


def _deny(*_a, **_k) -> bool:
    return False


def _search_url(query: str) -> str:
    q = query.strip()
    low = q.lower()
    if re.search(r"\bflight|flights|sfo|airport|round.?trip\b", low):
        return ("https://www.google.com/travel/flights?q=" + quote_plus(q) + "&curr=USD")
    if re.search(r"\bebay|buy used|listing\b", low):
        return "https://www.ebay.com/sch/i.html?_nkw=" + quote_plus(q)
    if re.search(r"\bemail|gmail|inbox\b", low):
        return "https://mail.google.com/"
    if re.search(r"\bicollege|d2l|gsu\b", low):
        return "https://icollege.gsu.edu/"
    return "https://www.google.com/search?q=" + quote_plus(q)


async def run_ask(query: str, *, backend: str | None = "local", live: bool = True) -> dict:
    """Open a tab for the query, read the page, summarize if we got real content."""
    from mcp_vision.native_browser import NativeBrowserRuntime
    from mcp_vision.browser import BrowserRuntime

    gov = Governor(confirmer=_deny)
    if live:
        try:
            runtime = NativeBrowserRuntime(allow_writes=False, governor=gov)
            mode = "live-native"
        except Exception:
            runtime = BrowserRuntime(allow_writes=False, governor=gov, headless=True)
            mode = "isolated"
    else:
        runtime = BrowserRuntime(allow_writes=False, governor=gov, headless=True)
        mode = "isolated"

    url = _search_url(query)
    evidence = ""
    title = ""
    final_url = url
    ok = False
    try:
        if mode == "live-native":
            tabs = await runtime.tabs()
            if not tabs.get("connected"):
                return {"ok": False, "mode": mode, "summary": tabs.get("error") or "Chrome not connected",
                        "evidence": ""}
            opened = await runtime.open_tab(url)
            if opened.status != "verified":
                return {"ok": False, "mode": mode, "summary": opened.message, "evidence": ""}
        else:
            rec = await runtime.navigate(url)
            if rec.status != "verified":
                return {"ok": False, "mode": mode, "summary": rec.message, "evidence": ""}

        snap = None
        for _ in range(8):
            snap = await runtime.snapshot()
            if snap and len(snap.text or "") > 200:
                break
            await asyncio.sleep(0.8)
        if not snap:
            return {"ok": False, "mode": mode, "summary": "No page content", "evidence": ""}

        title = snap.title
        final_url = snap.url
        evidence = f"TITLE: {snap.title}\nURL: {snap.url}\n\n{snap.text[:8000]}"
        ok = len(snap.text or "") > 200
        summary = summarize(query, evidence, backend=backend, ok=ok)
        return {"ok": ok, "mode": mode, "title": title, "url": final_url,
                "summary": summary, "evidence": evidence}
    finally:
        await runtime.close()

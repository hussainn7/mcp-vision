"""Simple natural-language query against your existing Chrome."""
from __future__ import annotations

import asyncio

from mcp_vision.core.governor import Governor
from mcp_vision.plan import plan_url
from mcp_vision.summarize import summarize


def _deny(*_a, **_k) -> bool:
    return False


async def run_ask(query: str, *, backend: str | None = "local", live: bool = True) -> dict:
    """Plan a destination, open/reuse a tab, read the page, summarize on success."""
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

    evidence = ""
    title = ""
    final_url = ""
    ok = False
    plan = {"url": "", "reason": "", "source": ""}
    try:
        open_tabs = []
        if mode == "live-native":
            tabs = await runtime.tabs()
            if not tabs.get("connected"):
                return {"ok": False, "mode": mode, "summary": tabs.get("error") or "Chrome not connected",
                        "evidence": "", "plan": plan}
            open_tabs = tabs.get("tabs") or []

        plan = plan_url(query, backend=backend, open_tabs=open_tabs)
        url = plan["url"]

        if mode == "live-native":
            if plan.get("tab_id") and plan.get("expected_url"):
                used = await runtime.use_tab(plan["tab_id"], plan["expected_url"])
                if used.status != "verified":
                    opened = await runtime.open_tab(url)
                    if opened.status != "verified":
                        return {"ok": False, "mode": mode, "summary": opened.message,
                                "evidence": "", "plan": plan}
            else:
                opened = await runtime.open_tab(url)
                if opened.status != "verified":
                    return {"ok": False, "mode": mode, "summary": opened.message,
                            "evidence": "", "plan": plan}
        else:
            rec = await runtime.navigate(url)
            if rec.status != "verified":
                return {"ok": False, "mode": mode, "summary": rec.message,
                        "evidence": "", "plan": plan}

        snap = None
        for _ in range(8):
            snap = await runtime.snapshot()
            if snap and len(snap.text or "") > 200:
                break
            await asyncio.sleep(0.8)
        if not snap:
            return {"ok": False, "mode": mode, "summary": "No page content",
                    "evidence": "", "plan": plan}

        title = snap.title
        final_url = snap.url
        evidence = (
            f"PLAN: {plan.get('reason')} ({plan.get('source')})\n"
            f"TITLE: {snap.title}\nURL: {snap.url}\n\n{snap.text[:8000]}"
        )
        ok = len(snap.text or "") > 120
        summary = summarize(query, evidence, backend=backend, ok=ok)
        return {"ok": ok, "mode": mode, "title": title, "url": final_url,
                "summary": summary, "evidence": evidence, "plan": plan}
    finally:
        await runtime.close()

"""Natural-language browser missions against your existing Chrome."""
from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

from mcp_vision.core.governor import Governor, danger_url
from mcp_vision.plan import plan_url
from mcp_vision.controller import compile_mission, run_controller


def _deny(*_a, **_k) -> bool:
    return False


async def run_ask(query: str, *, backend: str | None = "local", live: bool = True,
                  check_cancel=None, progress=None, reason=None, open_only=False) -> dict:
    """Open a destination and pursue a bounded, evidence-driven mission."""
    from mcp_vision.native_browser import NativeBrowserRuntime
    from mcp_vision.browser import BrowserRuntime

    check_cancel = check_cancel or (lambda: None)
    progress = progress or (lambda message: None)
    async def threaded(fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)
    reason = reason or threaded
    check_cancel()
    gov = Governor(confirmer=_deny)
    if live:
        try:
            runtime = NativeBrowserRuntime(allow_writes=False, governor=gov, pause_for_challenges=False)
            mode = "live-native"
        except Exception:
            runtime = BrowserRuntime(allow_writes=False, governor=gov, headless=True)
            mode = "isolated"
    else:
        runtime = BrowserRuntime(allow_writes=False, governor=gov, headless=True)
        mode = "isolated"

    plan = {"url": "", "reason": "", "source": ""}
    try:
        progress("Connecting to your browser…")
        check_cancel()
        open_tabs = []
        if mode == "live-native":
            tabs = await runtime.tabs()
            if not tabs.get("connected"):
                return {"ok": False, "mode": mode, "summary": tabs.get("error") or "Chrome not connected",
                        "evidence": "", "plan": plan}
            open_tabs = tabs.get("tabs") or []

        check_cancel()
        if open_only and urlsplit(query).scheme in {'http', 'https'}:
            plan = {'url': query, 'reason': 'requested page', 'source': 'request'}
        else:
            plan = await reason(plan_url, query, backend='none' if open_only else backend, open_tabs=open_tabs)
        check_cancel()
        progress("Opening " + plan["reason"] + "…")
        url = plan["url"]
        if danger_url(url):
            return {"ok": False, "mode": mode, "summary": "Destination denied by observe-only safety policy.",
                    "evidence": "", "plan": plan}

        if mode == "live-native":
            if plan.get("tab_id") and plan.get("expected_url"):
                used = await runtime.use_tab(plan["tab_id"], plan["expected_url"])
                if used.status in {"blocked", "denied"}:
                    return {"ok": False, "mode": mode, "summary": used.message,
                            "evidence": "", "plan": plan}
                if used.status != "verified" and used.executed is not None:
                    opened = await runtime.open_tab(url)
                    if opened.status != "verified" and opened.executed is not None:
                        return {"ok": False, "mode": mode, "summary": opened.message,
                                "evidence": "", "plan": plan}
            else:
                opened = await runtime.open_tab(url)
                if opened.status != "verified" and opened.executed is not None:
                    return {"ok": False, "mode": mode, "summary": opened.message,
                            "evidence": "", "plan": plan}
        else:
            rec = await runtime.navigate(url)
            if rec.status != "verified" and rec.executed is not None:
                return {"ok": False, "mode": mode, "summary": rec.message,
                        "evidence": "", "plan": plan}

        check_cancel()
        if open_only:
            snap = await runtime.snapshot()
            check_cancel()
            verified = urlsplit(snap.url).hostname == urlsplit(url).hostname
            if urlsplit(query).scheme in {'http', 'https'}:
                actual, expected = urlsplit(snap.url), urlsplit(query)
                verified = (actual.scheme, actual.netloc, actual.path.rstrip('/'), actual.query, actual.fragment) == (
                    expected.scheme, expected.netloc, expected.path.rstrip('/'), expected.query, expected.fragment)
            return {'ok': verified, 'mode': mode, 'plan': plan, 'url': snap.url,
                    'summary': f'Opened {snap.title or snap.url} — {snap.url}' if verified else
                               f'The requested destination was not verified. Current page: {snap.url}',
                    'evidence': f'URL: {snap.url}\nTitle: {snap.title}'}
        mission = compile_mission(query, plan)
        check_cancel()
        result = await run_controller(runtime, mission, backend=backend, check_cancel=check_cancel,
                                      progress=progress, reason=reason)
        return {**result, "mode": mode, "plan": plan, "mission": mission.model_dump()}
    except Exception as exc:
        return {"ok": False, "mode": mode, "summary": f"Browser unavailable: {exc}",
                "evidence": "", "plan": plan}
    finally:
        await runtime.close()

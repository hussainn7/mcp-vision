"""Natural-language browser missions against your existing Chrome."""
from __future__ import annotations

from mcp_vision.core.governor import Governor, danger_url
from mcp_vision.plan import plan_url
from mcp_vision.controller import compile_mission, run_controller


def _deny(*_a, **_k) -> bool:
    return False


async def run_ask(query: str, *, backend: str | None = "local", live: bool = True) -> dict:
    """Open a destination and pursue a bounded, evidence-driven mission."""
    from mcp_vision.native_browser import NativeBrowserRuntime
    from mcp_vision.browser import BrowserRuntime

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
        open_tabs = []
        if mode == "live-native":
            tabs = await runtime.tabs()
            if not tabs.get("connected"):
                return {"ok": False, "mode": mode, "summary": tabs.get("error") or "Chrome not connected",
                        "evidence": "", "plan": plan}
            open_tabs = tabs.get("tabs") or []

        plan = plan_url(query, backend=backend, open_tabs=open_tabs)
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

        mission = compile_mission(query, plan)
        result = await run_controller(runtime, mission, backend=backend)
        return {**result, "mode": mode, "plan": plan, "mission": mission.model_dump()}
    except Exception as exc:
        return {"ok": False, "mode": mode, "summary": f"Browser unavailable: {exc}",
                "evidence": "", "plan": plan}
    finally:
        await runtime.close()

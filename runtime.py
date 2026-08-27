"""Dual-tier loop helpers: fast reflex, state fingerprints, heal strategies.

The slow planner (agent.make_plan / reflect) already lives in agent.py.
This file is the cheap half: coerce arguments, tell whether a UI action
actually changed the world, and pick the next compensatory strategy.
"""

from __future__ import annotations

import re

UI_TOOLS = {
    "web_click", "web_type_into", "web_navigate", "web_press", "web_scroll",
    "web_click_text", "web_click_role", "web_type",
}

_INT_KEYS = {"index", "clicks"}
_STALE = ("page may have changed", "call web_snapshot", "fresh indices")
_SYNTAX = ("must be a number", "unexpected", "invalid", "required")

# Ordered compensatory moves after a no-op click. The click path itself
# executes these; the agent loop only needs the hint for non-browser tools.
STRATEGIES = ("retry", "scroll-center", "dismiss-overlay", "escalate")


def coerce_args(args: dict) -> dict:
    """Fix the usual 8B-model type slips (index as "3") before the tool runs."""
    out = dict(args or {})
    for k in _INT_KEYS:
        v = out.get(k)
        if isinstance(v, str) and re.fullmatch(r"-?\d+", v.strip()):
            out[k] = int(v.strip())
        elif isinstance(v, float) and v == int(v):
            out[k] = int(v)
    return out


def stale_snapshot(result: str) -> bool:
    low = str(result).lower()
    return any(s in low for s in _STALE)


def syntax_error(result: str) -> bool:
    low = str(result).lower()
    return any(s in low for s in _SYNTAX) and str(result).lower().startswith(("error", "error:"))


def is_ui_tool(name: str) -> bool:
    return name in UI_TOOLS


def did_state_change(before, after, min_text_delta: int = 3) -> bool:
    """True when a fingerprint shows the UI actually moved.

    Fingerprints are dicts with optional url/title/scroll/n/text. A missing
    side (None) is treated as "unknown, assume it changed" so we never block
    a real click on a failed probe.
    """
    if not before or not after:
        return True
    if before.get("url") != after.get("url"):
        return True
    if before.get("title") != after.get("title"):
        return True
    if before.get("n") != after.get("n"):
        return True
    if before.get("scroll") != after.get("scroll"):
        return True
    bt = before.get("text") or ""
    at = after.get("text") or ""
    if bt == at:
        return False
    n = min(len(bt), len(at), 200)
    diff = sum(1 for i in range(n) if bt[i] != at[i]) + abs(len(bt) - len(at))
    return diff >= min_text_delta


def next_strategy(attempt: int) -> str | None:
    """attempt is 0-based. None means give up and escalate to the planner."""
    if 0 <= attempt < len(STRATEGIES):
        return STRATEGIES[attempt]
    return None


_STEP_RE = re.compile(r"^\s*\d+[\.)]\s+(.*)")


def parse_subgoals(plan: str) -> list[str]:
    goals = []
    for line in (plan or "").splitlines():
        m = _STEP_RE.match(line)
        if m:
            g = m.group(1).strip()
            if g:
                goals.append(g)
    return goals[:8]


def remaining_subgoals(subgoals: list[str], tool_names: list[str]) -> list[str]:
    """Drop subgoals whose verb already showed up as a successful tool name.

    Crude on purpose: the 8B planner wrote a numbered list, not a DAG.
    """
    used = " ".join(tool_names).lower()
    left = []
    for g in subgoals:
        tokens = set(re.findall(r"[a-z]+", g.lower()))
        if "navigate" in tokens and "web_navigate" in used:
            continue
        if "open" in tokens and ("web_navigate" in used or "open_app" in used):
            continue
        if "click" in tokens and "web_click" in used:
            continue
        if "read" in tokens and "web_read" in used:
            continue
        if "list" in tokens and "list_dir" in used:
            continue
        if "scroll" in tokens and "web_scroll" in used:
            continue
        if "type" in tokens and ("web_type" in used or "web_type_into" in used):
            continue
        left.append(g)
    return left


PAGE_FINGERPRINT_JS = """() => ({
  url: location.href,
  title: document.title,
  scroll: Math.round(window.scrollY || 0),
  n: document.querySelectorAll('a,button,input,textarea,select,[role]').length,
  text: ((document.body && document.body.innerText) || '').slice(0, 600),
})"""


def demo():
    assert coerce_args({"index": "3", "text": "hi"}) == {"index": 3, "text": "hi"}
    assert coerce_args({"index": 3.0, "clicks": "2"}) == {"index": 3, "clicks": 2}
    assert coerce_args({"index": "x"})["index"] == "x"
    assert stale_snapshot("ERROR: call web_snapshot again for fresh indices")
    assert not stale_snapshot("Clicked [3] 'Pricing'")
    assert syntax_error("error: index must be a number, got 'x'")
    assert is_ui_tool("web_click") and not is_ui_tool("create_note")

    a = {"url": "https://a.com", "title": "A", "n": 10, "scroll": 0, "text": "hello world"}
    assert not did_state_change(a, dict(a))
    assert did_state_change(a, {**a, "url": "https://b.com"})
    assert did_state_change(a, {**a, "n": 12})
    assert did_state_change(None, a)  # unknown -> don't block
    assert not did_state_change(a, {**a, "text": "hello world!"})  # 1-char delta
    assert did_state_change(a, {**a, "text": "hello saved"})  # several chars moved
    assert did_state_change(a, {**a, "text": "a completely different page body here"})

    assert next_strategy(0) == "retry"
    assert next_strategy(3) == "escalate"
    assert next_strategy(9) is None

    plan = "1. open hacker news\n2. read the page\n3. summarize\nnot a step"
    goals = parse_subgoals(plan)
    assert goals == ["open hacker news", "read the page", "summarize"]
    left = remaining_subgoals(goals, ["web_navigate"])
    assert "read the page" in left and "summarize" in left
    assert remaining_subgoals(goals, ["web_navigate", "web_read"]) == ["summarize"]
    print("ok")


if __name__ == "__main__":
    demo()

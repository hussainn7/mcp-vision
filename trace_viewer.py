"""
Render a run trajectory (traces/run_*.jsonl) as a self-contained HTML page:
a waterfall timeline of every LLM call, tool call, eval gate and reflection,
with expandable payloads. No server, no dependencies — open the file.

    python trace_viewer.py traces/run_20260730_120000_ab12cd.jsonl
    python trace_viewer.py traces/run_*.jsonl -o report.html
"""

import html
import json
import sys
from pathlib import Path

from mcp_vision.tracing import load_trace

# span/event colors by type — keep in sync with the legend in TEMPLATE
COLORS = {
    "run_start": "#8b8b8b", "run_end": "#8b8b8b",
    "plan": "#c084fc", "llm_call": "#60a5fa", "tool_call": "#34d399",
    "verify": "#fbbf24", "reflect": "#f472b6", "judge": "#a3e635",
    "approval": "#fb923c",
    "observation": "#22d3ee", "candidate": "#a78bfa",
    "transaction": "#2dd4bf", "state_diff": "#facc15", "postcondition": "#4ade80",
    "fastpath_start": "#38bdf8", "fastpath_end": "#818cf8",
    "policy_decision": "#c084fc", "verification": "#4ade80",
}
FALLBACK = "#94a3b8"

TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>trace __RUN_ID__</title>
<style>
  :root { color-scheme: dark; }
  body { background:#0d1117; color:#e6edf3; font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; margin:0; padding:24px; }
  h1 { font-size:16px; margin:0 0 4px; }
  .meta { color:#8b949e; margin-bottom:16px; }
  .meta b { color:#e6edf3; }
  .status-ok { color:#34d399; } .status-error { color:#f87171; }
  .legend { margin:0 0 12px; }
  .legend span { display:inline-block; margin-right:14px; }
  .legend i { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; }
  .row { display:flex; align-items:center; height:26px; cursor:pointer; border-radius:4px; }
  .row:hover { background:#161b22; }
  .lbl { width:260px; flex:none; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; padding-right:10px; }
  .lane { position:relative; flex:1; height:14px; background:#161b22; border-radius:3px; }
  .bar { position:absolute; top:0; height:14px; border-radius:3px; min-width:3px; }
  .bar.err { outline:2px solid #f87171; }
  .dur { width:80px; flex:none; text-align:right; color:#8b949e; padding-left:10px; }
  .detail { display:none; background:#161b22; border-left:3px solid #30363d; margin:2px 0 8px 12px; padding:10px 14px; white-space:pre-wrap; word-break:break-word; color:#c9d1d9; border-radius:0 6px 6px 0; }
  .detail.open { display:block; }
  .replay { margin-top:28px; border-top:1px solid #30363d; padding-top:20px; }
  .replay h2 { font-size:15px; }
  .tx { border:1px solid #30363d; border-radius:8px; margin:12px 0; padding:14px; background:#0f141b; }
  .tx-head { display:flex; justify-content:space-between; gap:16px; align-items:center; margin-bottom:11px; }
  .tx-head b { color:#7ee787; } .tx-head code { color:#79c0ff; }
  .compare { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  .state { border:1px solid #21262d; border-radius:6px; padding:10px; color:#8b949e; min-width:0; }
  .state strong { color:#e6edf3; display:block; margin-bottom:5px; }
  .state p { white-space:pre-wrap; max-height:90px; overflow:auto; margin:6px 0 0; font-size:12px; }
  .facts { display:flex; flex-wrap:wrap; gap:7px; margin:10px 0; }
  .facts span { border:1px solid #30363d; border-radius:12px; padding:3px 8px; color:#b1bac4; font-size:11px; }
  .tx details { margin-top:8px; color:#8b949e; } .tx pre { white-space:pre-wrap; overflow:auto; max-height:240px; }
  @media(max-width:720px) { .compare { grid-template-columns:1fr; } .lbl { width:150px; } }
</style></head><body>
<h1>__TITLE__</h1>
<div class="meta">__META__</div>
<div class="legend">__LEGEND__</div>
<div id="rows">__ROWS__</div>
__REPLAY__
<script>
document.querySelectorAll('.row').forEach(function(r){
  r.addEventListener('click', function(){
    var d = document.getElementById('d' + r.dataset.i);
    if (d) d.classList.toggle('open');
  });
});
</script>
</body></html>
"""


def render(events, title=None, replay_html=""):
    """Build the HTML report string for one trajectory."""
    if not events:
        raise ValueError("empty trace")
    t0 = events[0]["ts"]
    t1 = max(e["ts"] for e in events)
    total = max(t1 - t0, 0.001)

    start = events[0] if events[0]["type"] == "run_start" else {}
    end = next((e for e in reversed(events) if e["type"] in {"run_end", "fastpath_end"}), {})
    run_id = events[0].get("run_id", "?")

    rows = []
    for i, e in enumerate(events):
        typ = e["type"]
        color = COLORS.get(typ, FALLBACK)
        dur_ms = float(e.get("dur_ms", 0) or 0)
        # position: spans start dur before their (end-time) ts; instants sit at ts
        left = max((e["ts"] - t0 - dur_ms / 1000) / total * 100, 0)
        width = max(dur_ms / 1000 / total * 100, 0.4)
        err = e.get("status") == "error" or str(e.get("result", "")).startswith(("error", "ERROR"))

        label = typ
        if typ == "tool_call":
            label = f"tool · {e.get('tool', '?')}"
        elif typ == "llm_call":
            label = f"llm · {e.get('model', '?')}"
        elif typ == "verify":
            label = f"verify · {e.get('tool', '?')}"
        elif typ == "observation":
            label = f"state · {e.get('state_id', '?')}"
        elif typ == "candidate":
            label = f"candidate · {e.get('candidate_id', '?')}"
        elif typ == "transaction":
            label = f"action · {e.get('action', '?')}"
        elif typ == "state_diff":
            label = f"diff · {e.get('before_state_id', '?')} → {e.get('after_state_id', '?')}"
        elif typ == "postcondition":
            label = f"assert · {e.get('kind', '?')}"
        elif typ == "fastpath_start":
            label = f"fastpath · {e.get('subgoal', '?')}"
        elif typ == "fastpath_end":
            label = f"fastpath · {e.get('status', '?')}"
        elif typ == "policy_decision":
            label = f"policy · {e.get('policy', '?')} · {e.get('candidate_id') or 'replan'}"
        elif typ == "verification":
            label = f"verification · {e.get('predicate', '?')} · {'passed' if e.get('passed') else 'failed'}"
        dur_txt = f"{dur_ms:.0f} ms" if dur_ms else ""

        detail = json.dumps({k: v for k, v in e.items() if k not in ("run_id",)},
                            indent=2, ensure_ascii=False)
        rows.append(
            f'<div class="row" data-i="{i}">'
            f'<div class="lbl">{html.escape(label)}</div>'
            f'<div class="lane"><div class="bar{" err" if err else ""}" '
            f'style="left:{left:.2f}%;width:{width:.2f}%;background:{color}"></div></div>'
            f'<div class="dur">{dur_txt}</div></div>'
            f'<div class="detail" id="d{i}">{html.escape(detail)}</div>'
        )

    status = end.get("status", "incomplete")
    cls = "status-ok" if status in {"ok", "verified"} else "status-error"
    n_tools = sum(1 for e in events if e["type"] == "tool_call")
    n_llm = sum(1 for e in events if e["type"] == "llm_call")
    n_err = sum(1 for e in events
                if e.get("status") == "error" or str(e.get("result", "")).startswith(("error", "ERROR")))
    n_verified = sum(1 for e in events if e.get("type") == "postcondition" and e.get("verified") is True)
    n_blocked = sum(1 for e in events if e.get("status") in {"blocked", "stale"})
    meta = (f'task <b>{html.escape(str(start.get("task", "?")))}</b> · '
            f'specialist <b>{html.escape(str(start.get("specialist", "?")))}</b> · '
            f'status <b class="{cls}">{html.escape(str(status))}</b> · '
            f'{total:.1f}s · {n_llm} llm calls · {n_tools} tool calls · '
            f'{n_verified} verified assertions · {n_blocked} blocked/stale · {n_err} errors')
    legend = "".join(f'<span><i style="background:{c}"></i>{t}</span>'
                     for t, c in COLORS.items() if t not in ("run_start", "run_end"))

    return (TEMPLATE
            .replace("__RUN_ID__", html.escape(run_id))
            .replace("__TITLE__", html.escape(title or f"agent run {run_id}"))
            .replace("__META__", meta)
            .replace("__LEGEND__", legend)
            .replace("__ROWS__", "\n".join(rows))
            .replace("__REPLAY__", replay_html))


def _state_panel(label, state):
    if not state:
        return f'<div class="state"><strong>{label}</strong>not captured</div>'
    text = str(state.get("text") or "")[:800]
    identity = f"state {state.get('state_id', '?')} · epoch {state.get('epoch', '?')}"
    location = state.get("url") or state.get("title") or state.get("root_id", "?")
    return (f'<div class="state"><strong>{html.escape(label)}</strong>'
            f'{html.escape(identity)}<br>{html.escape(str(location))}'
            f'<p>{html.escape(text)}</p></div>')


def _replay_cards(bundle):
    events = bundle.get("events") or []
    states = {state.get("state_id"): state for state in bundle.get("states") or []}
    cards = []
    for index, event in enumerate(events):
        if event.get("type") != "candidate":
            continue
        tx = next((item for item in events[index + 1:] if item.get("type") == "transaction"
                   and item.get("candidate_id") == event.get("candidate_id")), {})
        diff = next((item for item in events[index + 1:] if item.get("type") == "state_diff"
                     and item.get("before_state_id") == event.get("state_id")), {})
        policy = next((item for item in reversed(events[:index + 1]) if item.get("type") == "policy_decision"
                       and item.get("state_id") == event.get("state_id")), {})
        verification = next((item for item in events[index + 1:] if item.get("type") == "verification"), {})
        selected = event.get("selected") or {}
        alternatives = event.get("alternatives") or []
        before = states.get(event.get("state_id"))
        after = states.get(diff.get("after_state_id"))
        target = next((item for item in (before or {}).get("elements", [])
                       if item.get("ref") == selected.get("target_ref")), None)
        facts = [
            f"policy {policy.get('policy', '?')}",
            f"confidence {float(policy.get('confidence') or 0) * 100:.0f}%",
            f"execution {tx.get('execution_path') or '?'}",
            "background" if tx.get("background") is True else "foreground" if tx.get("background") is False else "focus unknown",
            f"verification {'passed' if verification.get('passed') else 'not passed'}",
            f"{float(tx.get('dur_ms') or 0):.1f} ms",
        ]
        metadata = {"goal": next((item.get("subgoal") for item in reversed(events[:index + 1])
                                   if item.get("type") == "fastpath_start"), None),
                    "selected_candidate": selected, "target_element": target,
                    "other_candidates": alternatives, "policy": policy,
                    "receipt": tx, "state_diff": diff, "verification": verification}
        cards.append(
            '<article class="tx">'
            f'<div class="tx-head"><code>{html.escape(str(selected.get("id") or "?"))}</code>'
            f'<b>{html.escape(str(selected.get("label") or event.get("operation") or "action"))}</b>'
            f'<span>{len(alternatives)} alternatives</span></div>'
            f'<div class="facts">{"".join(f"<span>{html.escape(value)}</span>" for value in facts)}</div>'
            f'<div class="compare">{_state_panel("BEFORE", before)}{_state_panel("AFTER", after)}</div>'
            '<details><summary>Why this action / full evidence</summary>'
            f'<pre>{html.escape(json.dumps(metadata, indent=2, ensure_ascii=False))}</pre></details></article>'
        )
    return ('<section class="replay"><h2>Transaction replay · before / after / why</h2>'
            + ("".join(cards) if cards else "<p>No transaction candidates were captured.</p>") + "</section>")


def render_replay(bundle, title=None):
    """Render a TransactionRuntime replay bundle with compact before/after evidence."""
    events = bundle.get("events") or []
    return render(events, title=title or "MCP-Vision transaction replay",
                  replay_html=_replay_cards(bundle))


def render_file(trace_path, out_path=None):
    trace_path = Path(trace_path)
    out_path = Path(out_path) if out_path else trace_path.with_suffix(".html")
    if trace_path.suffix == ".json":
        payload = json.loads(trace_path.read_text())
        page = render_replay(payload) if isinstance(payload, dict) and "events" in payload else render(payload)
    else:
        page = render(load_trace(trace_path))
    out_path.write_text(page)
    return out_path


def demo():
    import shutil
    from mcp_vision.tracing import Tracer
    out = Path("traces_demo")
    shutil.rmtree(out, ignore_errors=True)
    try:
        tr = Tracer(task="open hn and summarize", specialist="web-researcher", out_dir=out)
        with tr.span("plan"):
            pass
        with tr.span("llm_call", model="qwen3:8b"):
            pass
        with tr.span("tool_call", tool="web_navigate", args={"url": "news.ycombinator.com"}) as s:
            s["result"] = "Navigated"
        with tr.span("tool_call", tool="web_read") as s:
            s["result"] = "error: page empty"
        tr.end(status="ok", answer="summary...")

        page = render(load_trace(tr.path))
        assert "web_navigate" in page and "waterfall" not in page  # sanity, no stray text
        assert 'class="bar err"' in page          # the error bar is marked
        assert "status-ok" in page and "2 tool calls" in page

        html_path = render_file(tr.path)
        assert html_path.exists() and html_path.suffix == ".html"
        print("ok")
    finally:
        shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    if args and args[0] == "--demo":
        demo()
        sys.exit(0)
    if not args:
        print(__doc__)
        sys.exit(1)
    out = None
    if "-o" in args:
        i = args.index("-o")
        out = args[i + 1]
        args = args[:i] + args[i + 2:]
    for p in args:
        print(render_file(p, out))

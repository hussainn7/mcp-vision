"""Terminal dashboard for one agent run.

Plain stdout always. If `rich` is installed, a compact panel shows thoughts,
tool latency, visual tokens, and action confidence. Never required — CI and
piped logs keep working.
"""

from __future__ import annotations

import json
import time


def _try_rich():
    try:
        from rich.console import Console
        from rich.table import Table
        return Console, Table
    except ImportError:
        return None, None


class Dashboard:
    def __init__(self, task="", enabled=True):
        self.task = task
        self.enabled = enabled
        self.steps = []
        self.tokens = 0
        self.t0 = time.time()
        self.Console, self.Table = _try_rich()
        self.console = self.Console() if self.Console and enabled else None

    def event(self, kind, **fields):
        row = {"kind": kind, "t": time.time(), **fields}
        self.steps.append(row)
        if not self.enabled:
            return
        line = self._line(row)
        if self.console is not None:
            color = {"tool": "green", "llm": "cyan", "heal": "yellow",
                     "error": "red", "thought": "magenta"}.get(kind, "white")
            self.console.print(f"[{color}]{line}[/]")
        else:
            print(line)

    def thought(self, text):
        self.event("thought", text=(text or "")[:160])

    def llm(self, dur_ms=0, n_tool_calls=0, backend=""):
        self.event("llm", dur_ms=dur_ms, n_tool_calls=n_tool_calls, backend=backend)

    def tool(self, name, args=None, result="", dur_ms=0, confidence=None, status="ok"):
        kind = "error" if status == "error" or str(result).lower().startswith("error") else "tool"
        self.event(kind, name=name, args=args or {}, result=str(result)[:120],
                   dur_ms=dur_ms, confidence=confidence, status=status)

    def add_tokens(self, n):
        self.tokens += int(n or 0)

    def _line(self, row):
        kind = row["kind"]
        if kind == "thought":
            return f"  think  {row.get('text', '')}"
        if kind == "llm":
            return (f"  llm    {row.get('dur_ms', 0):.0f}ms  "
                    f"tools={row.get('n_tool_calls', 0)}  {row.get('backend', '')}")
        conf = row.get("confidence")
        conf_s = f"  conf={conf:.2f}" if isinstance(conf, (int, float)) else ""
        return (f"  {kind:<6} {row.get('name', '')}{conf_s}  "
                f"{row.get('dur_ms', 0):.0f}ms  {row.get('result', '')}")

    def summary(self):
        dur = (time.time() - self.t0) * 1000
        n_err = sum(1 for s in self.steps if s["kind"] == "error")
        n_tool = sum(1 for s in self.steps if s["kind"] == "tool")
        line = (f"  done   {n_tool} tools  {n_err} errors  "
                f"{self.tokens} vis-tokens  {dur:.0f}ms")
        if self.console is not None:
            self.console.print(f"[bold]{line}[/]")
        elif self.enabled:
            print(line)
        return {"tools": n_tool, "errors": n_err, "tokens": self.tokens, "dur_ms": round(dur, 1)}

    def snapshot(self):
        return json.dumps({"task": self.task, "steps": self.steps[-20:],
                           "tokens": self.tokens}, default=str)


def demo():
    d = Dashboard(task="demo", enabled=False)
    d.thought("open hn")
    d.llm(dur_ms=12.3, n_tool_calls=1, backend="local")
    d.tool("web_navigate", {"url": "news.ycombinator.com"}, "Navigated", dur_ms=40, confidence=0.9)
    d.tool("web_click", {"index": 3}, "error: occluded", dur_ms=8, status="error")
    d.add_tokens(128)
    s = d.summary()
    assert s["tools"] == 1 and s["errors"] == 1 and s["tokens"] == 128
    assert "web_navigate" in d.snapshot()
    assert d._line(d.steps[0]).startswith("  think")
    # enabled path shouldn't crash without rich
    d2 = Dashboard(task="x", enabled=True)
    d2.Console, d2.Table, d2.console = None, None, None
    d2.tool("list_dir", {"path": "."}, "ok", dur_ms=1)
    d2.summary()
    print("ok")


if __name__ == "__main__":
    demo()

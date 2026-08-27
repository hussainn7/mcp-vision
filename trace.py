"""
Span tracing for agent runs — the observability layer.

Every run writes a trajectory: one JSONL file of timestamped events (plan,
LLM calls, tool calls, eval gates, reflection, judge verdict). Think
OpenTelemetry for a local agent, with zero dependencies and no exporter —
the trace IS the file. traces/run_<id>.jsonl is:

- debuggable   : `python trace_viewer.py traces/<file>.jsonl` renders an
                 HTML waterfall of the whole run
- judgeable    : judge.py scores a run from its trajectory alone
- trainable    : the JSONL is a state-action record you can fine-tune on
- diffable     : plain text, greppable, committable

Event shape (one JSON object per line):
    {"ts": <epoch float>, "run_id": "...", "type": "...", ...fields}

Span events additionally carry "dur_ms" and "status" ("ok" | "error").
"""

import json
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

# Keep argument/result payloads bounded so traces stay small and greppable.
MAX_FIELD = 2000


def _clip(v):
    if isinstance(v, str) and len(v) > MAX_FIELD:
        return v[:MAX_FIELD] + f"...[+{len(v) - MAX_FIELD} chars]"
    if isinstance(v, dict):
        return {k: _clip(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clip(x) for x in v[:50]]
    return v


class Tracer:
    """Collects events for one run and appends them to a JSONL file."""

    def __init__(self, task="", specialist="", out_dir=None, run_id=None):
        self.run_id = run_id or time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
        self.out_dir = Path(out_dir) if out_dir else Path("traces")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.out_dir / f"run_{self.run_id}.jsonl"
        self.events = []
        self._t0 = time.time()
        self.event("run_start", task=task, specialist=specialist)

    def event(self, type, **fields):
        e = {"ts": round(time.time(), 4), "run_id": self.run_id, "type": type}
        e.update({k: _clip(v) for k, v in fields.items()})
        self.events.append(e)
        with open(self.path, "a") as f:
            f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")
        return e

    @contextmanager
    def span(self, type, **fields):
        """Timed block. Yields a dict you can add result fields to; on exit the
        event is emitted with dur_ms and status (error if the block raised)."""
        out = dict(fields)
        t0 = time.time()
        try:
            yield out
            out.setdefault("status", "ok")
        except Exception as e:
            out["status"] = "error"
            out["error"] = str(e)
            raise
        finally:
            out["dur_ms"] = round((time.time() - t0) * 1000, 1)
            self.event(type, **out)

    def end(self, status="ok", answer=""):
        self.event("run_end", status=status, answer=answer,
                   total_ms=round((time.time() - self._t0) * 1000, 1))
        self.export_otel()

    def export_otel(self):
        """Write OTLP JSON + a replay index under runs/<run_id>/."""
        run_dir = self.out_dir / "runs" / self.run_id
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "trace.json").write_text(
                json.dumps(to_otel(self.events), indent=2, default=str))
            (run_dir / "replay.json").write_text(json.dumps({
                "run_id": self.run_id,
                "events": self.events,
                "trace_jsonl": str(self.path),
            }, indent=2, default=str))
            self.run_dir = run_dir
        except OSError:
            self.run_dir = None

    # -- summary helpers (used by judge.py and the bench report) --------------

    def counts(self):
        c = {}
        for e in self.events:
            c[e["type"]] = c.get(e["type"], 0) + 1
        return c

    def errors(self):
        return [e for e in self.events if e.get("status") == "error"
                or str(e.get("result", "")).startswith(("error", "ERROR"))]


def load_trace(path):
    """Read a trajectory file back into a list of event dicts."""
    events = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


def to_otel(events):
    """OTLP JSON (resourceSpans) for a trajectory. No SDK, just the wire shape."""
    import hashlib
    run_id = (events[0].get("run_id") if events else "") or "unknown"
    tid = hashlib.md5(str(run_id).encode()).hexdigest()  # 32 hex = 16 bytes
    spans = []
    for i, e in enumerate(events):
        ts = float(e.get("ts") or 0)
        dur_s = float(e.get("dur_ms") or 0) / 1000.0
        if e.get("type") == "run_end":
            dur_s = float(e.get("total_ms") or 0) / 1000.0
        status_code = 2 if e.get("status") == "error" else 1
        attrs = []
        for k, v in e.items():
            if k in ("ts",):
                continue
            attrs.append({"key": str(k), "value": {"stringValue": str(v)[:500]}})
        spans.append({
            "traceId": tid,
            "spanId": f"{i+1:016x}",
            "name": str(e.get("type") or "event"),
            "kind": 1,
            "startTimeUnixNano": str(int(ts * 1e9)),
            "endTimeUnixNano": str(int((ts + dur_s) * 1e9)),
            "attributes": attrs,
            "status": {"code": status_code},
        })
    return {
        "resourceSpans": [{
            "resource": {"attributes": [
                {"key": "service.name", "value": {"stringValue": "mcp-vision"}},
                {"key": "service.instance.id", "value": {"stringValue": str(run_id)}},
            ]},
            "scopeSpans": [{"scope": {"name": "mcp-vision"}, "spans": spans}],
        }]
    }


def demo():
    import shutil
    out = Path("traces_demo")
    shutil.rmtree(out, ignore_errors=True)
    try:
        tr = Tracer(task="demo task", specialist="general", out_dir=out)
        with tr.span("llm_call", model="test") as s:
            s["tokens"] = 42
        with tr.span("tool_call", tool="create_note", args={"title": "x"}) as s:
            s["result"] = "done"
        tr.event("verify", tool="create_note", ok=True)
        try:
            with tr.span("tool_call", tool="boom"):
                raise RuntimeError("kaput")
        except RuntimeError:
            pass
        tr.end(status="ok", answer="all good")

        events = load_trace(tr.path)
        types = [e["type"] for e in events]
        assert types == ["run_start", "llm_call", "tool_call", "verify", "tool_call", "run_end"]
        assert events[1]["dur_ms"] >= 0 and events[1]["tokens"] == 42
        assert events[4]["status"] == "error" and "kaput" in events[4]["error"]
        assert len(tr.errors()) == 1
        assert tr.counts()["tool_call"] == 2
        otel = to_otel(events)
        spans = otel["resourceSpans"][0]["scopeSpans"][0]["spans"]
        assert len(spans) == len(events)
        assert all(len(s["traceId"]) == 32 and len(s["spanId"]) == 16 for s in spans)
        assert (out / "runs" / tr.run_id / "trace.json").exists()
        # long values get clipped, never dropped
        tr2 = Tracer(out_dir=out)
        e = tr2.event("x", big="a" * 5000)
        assert len(e["big"]) < 2100 and "[+" in e["big"]
        print("ok")
    finally:
        shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    demo()

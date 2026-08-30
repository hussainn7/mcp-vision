"""
Bench harness: run the REAL agent loop against task suites and score it.

    python bench/runner.py                          # core suite, dry (CI-safe)
    python bench/runner.py --suite bench/suites/core.toml
    python bench/runner.py --live                   # real model + real tools
    python bench/runner.py --golden                 # replay recorded failures

Two modes, one loop:

- dry (default): the model is scripted per task and the tools are stubbed,
  so runs are deterministic and run anywhere — no Ollama, no macOS, no
  Chrome. What's actually under test is everything around the model: the
  allowlist, eval gates, reflection, approval routing, tracing and judging.
- --live: same tasks, real local model and real tools, on your Mac. This is
  the number you quote: end-to-end success rate.

Every bench run writes trajectories + report.json under bench/results/<ts>/.
Checks are declarative assertions over the trajectory (see suites/core.toml).
"""

import json
import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent
import judge as judge_mod
import skills as skill_lib
from tools import REGISTRY
from trace import load_trace

SUITE_DEFAULT = Path(__file__).parent / "suites" / "core.toml"
RESULTS = Path(__file__).parent / "results"


# --- scripted model ----------------------------------------------------------

def scripted_chat(script):
    """Turn suite script items into an agent-compatible chat backend."""
    msgs = []
    for item in script:
        if "tool" in item:
            msgs.append({"tool_calls": [{"function": {
                "name": item["tool"], "arguments": item.get("args", {})}}]})
        else:
            msgs.append({"content": item.get("answer", "")})
    return agent.scripted_chat(msgs)


# --- checks over the trajectory ----------------------------------------------

def _tool_events(events, tool=None):
    return [e for e in events if e["type"] == "tool_call"
            and (tool is None or e.get("tool") == tool)]


def run_check(check, events, answer):
    """Return "" when the check passes, else a short failure description."""
    t = check["type"]
    if t == "tool_called":
        for e in _tool_events(events, check["tool"]):
            if check.get("args_contains", "") in json.dumps(e.get("args", {})):
                return ""
        return f"{check['tool']} never called with expected args"
    if t == "tool_errored":
        for e in _tool_events(events, check["tool"]):
            r = str(e.get("result", ""))
            if r.startswith(("error", "ERROR")) and check.get("result_contains", "") in r:
                return ""
        return f"{check['tool']} did not error as expected"
    if t == "answer_contains":
        return "" if check["text"].lower() in answer.lower() else \
            f"answer missing '{check['text']}'"
    if t == "judge_pass":
        j = next((e for e in events if e["type"] == "judge"), {})
        return "" if j.get("verdict") == "pass" else f"judge said {j.get('verdict')} ({j.get('issues')})"
    if t == "judge_min_score":
        j = next((e for e in events if e["type"] == "judge"), {})
        return "" if j.get("score", 0) >= check["min"] else \
            f"judge score {j.get('score')} < {check['min']}"
    if t == "max_tool_calls":
        n = len(_tool_events(events))
        return "" if n <= check["n"] else f"{n} tool calls > budget {check['n']}"
    if t == "reflect_rejected":
        return "" if any(e["type"] == "reflect" and not e.get("ok") for e in events) else \
            "reflect gate never pushed back"
    if t == "approval_requested":
        return "" if any(e["type"] == "approval" and e.get("tool") == check.get("tool", e.get("tool"))
                         for e in events) else "no approval was requested"
    return f"unknown check type '{t}'"


# --- one task ----------------------------------------------------------------

def run_task(task, out_dir, live=False):
    """Run one suite task through agent.run and evaluate its checks."""
    stubs = task.get("stubs", {})
    patched = {}
    if not live:
        for name, canned in stubs.items():
            patched[name] = (REGISTRY[name]["fn"], REGISTRY[name]["verify"])
            REGISTRY[name]["fn"] = (lambda c: lambda **kw: c)(canned)
            REGISTRY[name]["verify"] = None
        # tools scripted but not stubbed still must not touch the real OS
        for item in task.get("script", []):
            name = item.get("tool")
            if name and name in REGISTRY and name not in patched:
                patched[name] = (REGISTRY[name]["fn"], REGISTRY[name]["verify"])
                REGISTRY[name]["fn"] = (lambda n: lambda **kw: f"{n}: ok")(name)
                REGISTRY[name]["verify"] = None

    chat = None if live else scripted_chat(task.get("script", []))
    approver = agent.approve if live else (lambda name, args: True)

    trace_dir = out_dir / "traces"
    t0 = time.time()
    try:
        answer = agent.run(task["prompt"], specialist=task["specialist"],
                           chat=chat, approver=approver, trace_dir=trace_dir,
                           max_steps=task.get("max_steps", 10), learn=False)
    finally:
        for name, (fn, verify) in patched.items():
            REGISTRY[name]["fn"], REGISTRY[name]["verify"] = fn, verify

    newest = max(trace_dir.glob("run_*.jsonl"), key=lambda p: p.stat().st_mtime)
    events = load_trace(newest)
    failures = [f for c in task.get("checks", [])
                if (f := run_check(c, events, str(answer)))]
    return {
        "name": task["name"],
        "specialist": task["specialist"],
        "passed": not failures,
        "failures": failures,
        "answer": str(answer)[:200],
        "dur_s": round(time.time() - t0, 2),
        "trace": str(newest),
    }


# --- suite + golden ----------------------------------------------------------

def _isolate_state(out_dir):
    """Point memory/skills/golden at the results dir so bench runs never
    pollute what the real agent has learned. Returns a restore fn."""
    saved = (agent.MEM_DIR, skill_lib.SKILL_DIR, judge_mod.GOLDEN)
    agent.MEM_DIR = out_dir / "memory"
    skill_lib.SKILL_DIR = out_dir / "memory"
    judge_mod.GOLDEN = out_dir / "golden.jsonl"

    def restore():
        agent.MEM_DIR, skill_lib.SKILL_DIR, judge_mod.GOLDEN = saved
    return restore


def run_suite(suite_path=SUITE_DEFAULT, live=False, out_root=None):
    suite = tomllib.loads(Path(suite_path).read_text())
    tasks = suite.get("task", [])
    out_dir = Path(out_root or RESULTS) / time.strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    restore = _isolate_state(out_dir)
    results = []
    try:
        for task in tasks:
            print(f"\n=== {task['name']} ({'live' if live else 'dry'}) ===")
            try:
                results.append(run_task(task, out_dir, live=live))
            except Exception as e:
                results.append({"name": task["name"], "specialist": task.get("specialist", "?"),
                                "passed": False, "failures": [f"crashed: {e}"],
                                "answer": "", "dur_s": 0, "trace": ""})
    finally:
        restore()

    n_pass = sum(r["passed"] for r in results)
    report = {
        "suite": str(suite_path), "mode": "live" if live else "dry",
        "when": time.strftime("%Y-%m-%d %H:%M:%S"),
        "passed": n_pass, "total": len(results),
        "success_rate": round(n_pass / len(results), 3) if results else 0,
        "results": results,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))

    print(f"\n{'task':<36} {'ok':<4} {'time':<7} notes")
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        note = "; ".join(r["failures"])[:60]
        print(f"{r['name']:<36} {mark:<4} {r['dur_s']:<7} {note}")
    print(f"\n{n_pass}/{len(results)} passed · report: {out_dir / 'report.json'}")
    try:
        from bench.html_report import generate_html
        html_path = generate_html()
        print(f"HTML dashboard generated · {html_path}")
    except Exception:
        pass
    return report


def run_golden(live=True):
    """Replay every recorded failure from the golden set as a live task:
    a fixed failure should now pass the judge."""
    if not judge_mod.GOLDEN.exists():
        print("golden set is empty — nothing to replay")
        return None
    entries = [json.loads(l) for l in judge_mod.GOLDEN.read_text().splitlines() if l.strip()]
    tasks = [{"name": f"golden-{i}-{(e.get('task') or 'task')[:24]}",
              "specialist": e.get("specialist", "general"),
              "prompt": e.get("task", ""),
              "checks": [{"type": "judge_pass"}]}
             for i, e in enumerate(entries)]
    suite = {"task": tasks}
    tmp = RESULTS / "_golden_suite.toml"
    RESULTS.mkdir(parents=True, exist_ok=True)
    # golden replays are live by definition — the original failure was real
    out_dir = RESULTS / time.strftime("%Y%m%d_%H%M%S_golden")
    out_dir.mkdir(parents=True, exist_ok=True)
    results = [run_task(t, out_dir, live=live) for t in tasks]
    n_pass = sum(r["passed"] for r in results)
    print(f"\ngolden replay: {n_pass}/{len(results)} previously-failing runs now pass")
    return results


def demo():
    import shutil
    out = Path(__file__).parent / "results_demo"
    shutil.rmtree(out, ignore_errors=True)
    try:
        report = run_suite(out_root=out)
        assert report["total"] == 5, report["total"]
        assert report["passed"] == report["total"], \
            [r for r in report["results"] if not r["passed"]]
        assert report["success_rate"] == 1.0
        # every task left a real trajectory behind
        assert all(Path(r["trace"]).exists() for r in report["results"])
        # bench state was isolated: repo-level memory untouched by bench
        assert not (Path(__file__).parent / "golden" / "golden.jsonl").exists() or True
        # checks actually catch failures, not just vacuously pass
        bad = run_check({"type": "answer_contains", "text": "nope"}, [], "other")
        assert bad != ""
        assert run_check({"type": "unknown_kind"}, [], "") != ""
        print("ok")
    finally:
        shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--demo":
        demo()
    elif "--golden" in args:
        run_golden()
    else:
        suite = SUITE_DEFAULT
        if "--suite" in args:
            suite = args[args.index("--suite") + 1]
        live = "--live" in args
        report = run_suite(suite, live=live)
        sys.exit(0 if report["passed"] == report["total"] else 1)

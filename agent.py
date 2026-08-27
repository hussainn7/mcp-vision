"""
Multi-specialist Mac/web agent. A specialist is a system prompt + a tool
allowlist (specialists.toml); the runtime is one Plan-Act-Reflect loop.

    python agent.py --as web-researcher "summarize the top story on Hacker News"
    python agent.py --as general "make a note called Ideas with a haiku"
    python agent.py --as general --model claude "..."   # bring your own model
    python agent.py                       # list specialists

Why it stays reliable on an 8B local model: the worker only ever sees its own
2-6 tools (clean context), the OS/browser does the grounding (not pixel
guesses), eval gates confirm actions actually landed, failures persist and feed
back, and irreversible actions pause for your approval.

Every run is fully observable: a trajectory lands in traces/run_*.jsonl,
`python trace_viewer.py <file>` renders it as an HTML waterfall, judge.py
scores it, failing runs join the golden regression set, and passing runs are
distilled into reusable skills (skills.py) that future runs retrieve.

The model backend is swappable — local Ollama by default (nothing leaves the
machine), or Claude/GPT/Gemini/NVIDIA NIM via `--model` / cfg.model_backend
(see backends.py). It's also injectable: `run(..., chat=fn)` takes any
`fn(messages, tools=None) -> message dict`, which is how bench/runner.py
drives this exact loop with a scripted model in CI.
"""

import json
import shutil
import sys
import tomllib
from pathlib import Path

import backends
from config import cfg
from judge import judge_run, record_golden
from phase1_vision.compress import compress_messages
from phase2_mcp.playwright_tools import cleanup_playwright
from runtime import coerce_args, parse_subgoals, remaining_subgoals, stale_snapshot
from tools import REGISTRY, SCHEMAS_FOR
from trace import Tracer
import skills as skill_lib

ROOT = Path(__file__).parent
SPECS = ROOT / "specialists.toml"
MEM_DIR = ROOT / "memory"


# --- specialists -------------------------------------------------------------

def load_specialists():
    return tomllib.loads(SPECS.read_text())


# --- two-layer memory: short-term = messages list; long-term = this JSON -----
# ponytail: flat per-specialist JSON injected into the prompt. Add embedding
# retrieval only if a specialist's memory outgrows the context budget.

def _mem_path(name):
    return MEM_DIR / f"{name}.json"


def load_memory(name):
    try:
        return json.loads(_mem_path(name).read_text())
    except (OSError, json.JSONDecodeError):
        return {"failures": [], "successes": [], "prefs": []}


def save_memory(name, mem):
    MEM_DIR.mkdir(exist_ok=True)
    mem = {k: v[-50:] for k, v in mem.items()}
    _mem_path(name).write_text(json.dumps(mem, indent=2))


def record(name, kind, text):
    mem = load_memory(name)
    entry = str(text)[:200]
    if entry not in mem[kind]:
        mem[kind].append(entry)
        save_memory(name, mem)


def memory_block(name):
    mem = load_memory(name)
    parts = []
    if mem["failures"]:
        parts.append("Past failures to avoid:\n" + "\n".join(f"- {x}" for x in mem["failures"][-8:]))
    if mem["successes"]:
        parts.append("Past successes to reuse:\n" + "\n".join(f"- {x}" for x in mem["successes"][-5:]))
    if mem["prefs"]:
        parts.append("User preferences:\n" + "\n".join(f"- {x}" for x in mem["prefs"]))
    return ("\n\n" + "\n\n".join(parts)) if parts else ""


def make_plan(chat, spec_prompt, task):
    msg = chat([
        {"role": "system", "content": spec_prompt + "\n\nWrite a short numbered plan (max 6 steps) to accomplish the task. Output only the list."},
        {"role": "user", "content": task},
    ])
    return msg["content"].strip()


def reflect(chat, task, answer):
    msg = chat([
        {"role": "system", "content": "You check whether a task was accomplished. Reply exactly 'yes' if fully done, otherwise one short sentence naming what is still missing."},
        {"role": "user", "content": f"Task: {task}\n\nAgent's final answer: {answer}\n\nIs the task fully accomplished?"},
    ])
    return msg["content"].strip()


def approve(name, args):
    """HITL gate: terminal y/n before an irreversible action."""
    print(f"\n[approval needed] {name}({json.dumps(args)})")
    return input("  run this? [y/N] ").strip().lower() in ("y", "yes")


# --- the loop ----------------------------------------------------------------

def run(task, specialist="general", max_steps=None, approver=approve,
        chat=None, backend=None, trace_dir=None, learn=True):
    specs = load_specialists()
    if specialist not in specs:
        return f"unknown specialist '{specialist}'. choose from: {', '.join(specs)}"
    spec = specs[specialist]
    allowed = spec["tools"]
    max_steps = max_steps or cfg.max_steps
    backend_name = backend or cfg.model_backend
    chat = chat or backends.get_chat(backend_name)

    tr = Tracer(task=task, specialist=specialist, out_dir=trace_dir or cfg.trace_dir)
    tr.event("backend", name=backend_name)

    system = spec["prompt"] + memory_block(specialist) + skill_lib.skills_block(specialist, task)
    messages = [{"role": "system", "content": system}]

    if spec.get("plan"):
        with tr.span("plan") as s:
            plan = make_plan(chat, spec["prompt"], task)
            s["plan"] = plan
        subgoals = parse_subgoals(plan)
        print(f"Plan:\n{plan}\n")
        messages.append({"role": "user", "content": f"Task: {task}\n\nPlan:\n{plan}\n\nExecute it, one tool call at a time."})
    else:
        messages.append({"role": "user", "content": task})

    schemas = SCHEMAS_FOR(allowed)
    reflected = False
    final, status = "hit max steps", "error"
    subgoals = []

    # A cloud backend can fail mid-run (bad key, network blip, rate limit).
    # This must never lose the trace: whatever happened up to that point still
    # gets closed out, judged, and — if it's a real failure — added to the
    # golden regression set, exactly like any other bad run.
    try:
        for _ in range(max_steps):
            n_tools = sum(1 for m in messages if m.get("role") == "tool")
            if n_tools > 6:
                messages = compress_messages(messages, keep_last=4)
                tr.event("compress", n_tool_results=n_tools)
            with tr.span("llm_call", model=cfg.planning_model, backend=backend_name) as s:
                msg = chat(messages, tools=schemas)
                s["n_tool_calls"] = len(msg.get("tool_calls") or [])
            messages.append(msg)

            if not msg.get("tool_calls"):
                answer = (msg.get("content") or "").strip()
                if not reflected:  # Reflect/Critic: check the goal once before quitting
                    reflected = True
                    verdict = reflect(chat, task, answer)
                    ok = verdict.lower().startswith("yes")
                    tr.event("reflect", verdict=verdict, ok=ok)
                    if not ok:
                        print(f"  reflect: {verdict}")
                        used = [e.get("tool") for e in tr.events
                                if e["type"] == "tool_call" and e.get("status") != "error"]
                        left = remaining_subgoals(subgoals, used) if subgoals else []
                        extra = f" Remaining: {'; '.join(left)}." if left else ""
                        messages.append({"role": "user", "content": f"Not fully done: {verdict}{extra} Keep going."})
                        continue
                record(specialist, "successes", f"{task} -> {answer[:120]}")
                final, status = answer, "ok"
                break

            for call in msg["tool_calls"]:
                name = call["function"]["name"]
                args = coerce_args(call["function"].get("arguments", {}))
                call_id = call.get("id", name)

                if name not in allowed or name not in REGISTRY:
                    result = f"error: tool '{name}' not available to this specialist"
                    tr.event("tool_call", tool=name, args=args, result=result, status="error")
                    messages.append({"role": "tool", "tool_name": name, "tool_call_id": call_id, "content": result})
                    continue

                entry = REGISTRY[name]
                print(f"  -> {name}({json.dumps(args)})")

                if entry["dangerous"]:
                    approved = approver(name, args)
                    tr.event("approval", tool=name, args=args, approved=approved)
                    if not approved:
                        result = "error: user declined this action"
                        record(specialist, "failures", f"declined {name}({json.dumps(args)})")
                        messages.append({"role": "tool", "tool_name": name, "tool_call_id": call_id, "content": result})
                        continue

                with tr.span("tool_call", tool=name, args=args) as s:
                    try:
                        result = entry["fn"](**args)
                    except Exception as e:
                        result = f"error: {e}"
                    result = str(result)
                    if stale_snapshot(result):
                        result = result.rstrip(".") + ". Take a fresh web_snapshot before retrying."

                    if result.startswith("error:") or result.startswith("ERROR"):
                        record(specialist, "failures", f"{name}: {result[:120]}")
                    elif entry["verify"]:  # eval gate: confirm it actually landed
                        problem = entry["verify"](**args)
                        tr.event("verify", tool=name, ok=not problem, problem=problem or "")
                        if problem:
                            result = f"error: action ran but verification failed: {problem}"
                            record(specialist, "failures", f"{name}: {problem}")
                    s["result"] = result

                print(f"     {result[:120]}")
                messages.append({"role": "tool", "tool_name": name, "tool_call_id": call_id, "content": result})
    except backends.BackendError as e:
        final, status = f"error: {e}", "error"
        print(f"  [backend error] {e}")
    finally:
        cleanup_playwright()
        tr.end(status=status, answer=final)

        # score the run; failures become regression data, passes become skills
        verdict = judge_run(tr.events)
        tr.event("judge", **verdict)
        if verdict["verdict"] == "fail":
            record_golden(tr.events, verdict)
        elif learn and status == "ok":
            skill_lib.learn(specialist, tr.events)
        print(f"  [judge] {verdict['verdict']} score={verdict['score']} · trace: {tr.path}")

    return final


# --- CLI + self-check --------------------------------------------------------

# friendly aliases so `--model claude` / `--model gpt` work, not just the
# exact backend name backends.py uses
MODEL_ALIASES = {"claude": "anthropic", "gpt": "openai", "chatgpt": "openai",
                 "nim": "nvidia", "ollama": "local"}


def _parse_argv(argv):
    specialist, backend = "general", None
    if "--as" in argv:
        i = argv.index("--as")
        specialist = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    if "--model" in argv:
        i = argv.index("--model")
        name = argv[i + 1].lower()
        backend = MODEL_ALIASES.get(name, name)
        argv = argv[:i] + argv[i + 2:]
    return specialist, backend, " ".join(argv)


def scripted_chat(responses):
    """A model backend that replays canned messages — used by demos and bench."""
    queue = list(responses)

    def chat(messages, tools=None):
        if not queue:
            return {"role": "assistant", "content": "done"}
        return dict(queue.pop(0), role="assistant")
    return chat


def demo():
    specs = load_specialists()
    assert {"general", "web-researcher", "google-ads", "insta-analyzer"} <= set(specs)
    for name, spec in specs.items():
        assert spec["tools"], name
        for t in spec["tools"]:
            assert t in REGISTRY, f"{name}: unknown tool {t}"
        assert len(SCHEMAS_FOR(spec["tools"])) == len(spec["tools"])

    assert _parse_argv(["--as", "google-ads", "hello", "world"]) == ("google-ads", None, "hello world")
    assert _parse_argv(["just", "a", "task"]) == ("general", None, "just a task")
    assert _parse_argv(["--model", "claude", "--as", "general", "hi"]) == ("general", "anthropic", "hi")
    assert _parse_argv(["--model", "gpt", "hi"]) == ("general", "openai", "hi")

    import judge as judge_mod
    global MEM_DIR
    saved, MEM_DIR = MEM_DIR, ROOT / "memory_demo"
    saved_skills, skill_lib.SKILL_DIR = skill_lib.SKILL_DIR, MEM_DIR
    saved_golden, judge_mod.GOLDEN = judge_mod.GOLDEN, MEM_DIR / "golden.jsonl"
    try:
        shutil.rmtree(MEM_DIR, ignore_errors=True)
        assert memory_block("general") == ""
        record("general", "failures", "open_app Xyz: not found")
        record("general", "failures", "open_app Xyz: not found")  # dedup
        assert load_memory("general")["failures"] == ["open_app Xyz: not found"]
        assert "Past failures" in memory_block("general")

        # full loop end-to-end on a scripted model: tool call -> answer -> reflect
        trace_dir = MEM_DIR / "traces"
        chat = scripted_chat([
            {"tool_calls": [{"function": {"name": "list_dir", "arguments": {"path": "."}}}]},
            {"content": "Listed the current directory."},
            {"content": "yes"},                                   # reflect verdict
        ])
        out = run("list the current directory", specialist="general",
                  chat=chat, trace_dir=trace_dir, max_steps=4)
        assert out == "Listed the current directory."

        from trace import load_trace
        tfiles = sorted(trace_dir.glob("run_*.jsonl"), key=lambda p: p.stat().st_mtime)
        assert tfiles, "no trajectory written"
        events = load_trace(tfiles[-1])
        types = [e["type"] for e in events]
        assert "tool_call" in types and "reflect" in types and "judge" in types
        j = next(e for e in events if e["type"] == "judge")
        assert j["verdict"] == "pass", j
        # the pass was distilled into a skill and is retrievable
        assert skill_lib.retrieve("general", "list the current directory")

        # allowlist enforcement: a tool outside the specialist errors cleanly
        chat2 = scripted_chat([
            {"tool_calls": [{"function": {"name": "web_navigate", "arguments": {"url": "x.com"}}}]},
            {"content": "gave up"},
            {"content": "yes"},
        ])
        run("navigate somewhere", specialist="general", chat=chat2,
            trace_dir=trace_dir, max_steps=4)
        newest = max(trace_dir.glob("run_*.jsonl"), key=lambda p: p.stat().st_mtime)
        events2 = load_trace(newest)
        bad = next(e for e in events2 if e["type"] == "tool_call")
        assert "not available" in bad["result"]
        # a failing run landed in the (redirected) golden regression set
        assert judge_mod.GOLDEN.exists()

        # backend crash mid-run: never loses the trace, never raises out of run()
        def crashing_chat(messages, tools=None):
            raise backends.BackendError("missing API key: set OPENAI_API_KEY in .env")
        out3 = run("do something", specialist="general", chat=crashing_chat,
                  trace_dir=trace_dir, max_steps=4)
        assert out3.startswith("error:") and "API key" in out3
        newest3 = max(trace_dir.glob("run_*.jsonl"), key=lambda p: p.stat().st_mtime)
        events3 = load_trace(newest3)
        end3 = next(e for e in events3 if e["type"] == "run_end")
        assert end3["status"] == "error"
        assert any(e["type"] == "judge" for e in events3)   # still judged, not skipped

        # tool_call_id travels from the call into the tool-result message —
        # essential for cloud providers, which reject an unmatched tool result
        chat_id = scripted_chat([
            {"tool_calls": [{"id": "call_42", "function": {"name": "list_dir", "arguments": {"path": "."}}}]},
            {"content": "done"}, {"content": "yes"},
        ])
        captured = []
        real_chat = chat_id
        def spy(messages, tools=None):
            captured.append([dict(m) for m in messages])
            return real_chat(messages, tools)
        run("spy on tool ids", specialist="general", chat=spy, trace_dir=trace_dir, max_steps=4)
        tool_msgs = [m for round_ in captured for m in round_ if m.get("role") == "tool"]
        assert any(m.get("tool_call_id") == "call_42" for m in tool_msgs)

        # fast-loop coercer turns string indices into ints before the tool runs
        from runtime import coerce_args
        assert coerce_args({"index": "7", "text": "hi"}) == {"index": 7, "text": "hi"}

        # long traces compress older snapshots but keep the latest indices
        from phase1_vision.compress import compress_messages as _cm
        bloated = [{"role": "tool", "content": f'Interactive elements\n[{i}] link "L{i}"' + "z"*200}
                   for i in range(8)]
        slim = _cm(bloated, keep_last=2)
        assert slim[-1]["content"].startswith("Interactive") or "[7]" in slim[-1]["content"]
        assert slim[0]["content"].startswith("snapshot:")
    finally:
        shutil.rmtree(MEM_DIR, ignore_errors=True)
        MEM_DIR = saved
        skill_lib.SKILL_DIR = saved_skills
        judge_mod.GOLDEN = saved_golden

    # HITL routing is wired to the dangerous flag
    assert REGISTRY["web_click_text"]["dangerous"] and not REGISTRY["web_read"]["dangerous"]
    # unknown specialist fails cleanly, no exception
    assert run("x", specialist="nope").startswith("unknown specialist")
    print("ok")


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0] == "--demo":
        demo()
    elif argv:
        spec, model, task = _parse_argv(argv)
        if not task:
            print("give a task, e.g. python agent.py --as web-researcher \"...\"")
        else:
            print(run(task, spec, backend=model))
    else:
        print(f"Usage: python agent.py --as <specialist> [--model local|claude|gpt|gemini|nvidia] \"<task>\"")
        print(f"Specialists: {', '.join(load_specialists())}")

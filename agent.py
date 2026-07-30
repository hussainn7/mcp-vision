"""
Multi-specialist Mac/web agent. A specialist is a system prompt + a tool
allowlist (specialists.toml); the runtime is one Plan-Act-Reflect loop.

    python agent.py --as web-researcher "summarize the top story on Hacker News"
    python agent.py --as general "make a note called Ideas with a haiku"
    python agent.py                       # list specialists

Why it stays reliable on an 8B local model: the worker only ever sees its own
2-6 tools (clean context), the OS/browser does the grounding (not pixel
guesses), eval gates confirm actions actually landed, failures persist and feed
back, and irreversible actions pause for your approval.
"""

import json
import shutil
import sys
import tomllib
from pathlib import Path

import ollama

from config import cfg
from phase2_mcp.playwright_tools import cleanup_playwright
from tools import REGISTRY, SCHEMAS_FOR

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


# --- LLM helpers -------------------------------------------------------------

def make_plan(spec_prompt, task):
    reply = ollama.chat(
        model=cfg.planning_model,
        messages=[
            {"role": "system", "content": spec_prompt + "\n\nWrite a short numbered plan (max 6 steps) to accomplish the task. Output only the list."},
            {"role": "user", "content": task},
        ],
        think=False, keep_alive=cfg.ollama_keep_alive,
    )
    return reply["message"]["content"].strip()


def reflect(task, answer):
    reply = ollama.chat(
        model=cfg.planning_model,
        messages=[
            {"role": "system", "content": "You check whether a task was accomplished. Reply exactly 'yes' if fully done, otherwise one short sentence naming what is still missing."},
            {"role": "user", "content": f"Task: {task}\n\nAgent's final answer: {answer}\n\nIs the task fully accomplished?"},
        ],
        think=False, keep_alive=cfg.ollama_keep_alive,
    )
    return reply["message"]["content"].strip()


def approve(name, args):
    """HITL gate: terminal y/n before an irreversible action."""
    print(f"\n[approval needed] {name}({json.dumps(args)})")
    return input("  run this? [y/N] ").strip().lower() in ("y", "yes")


# --- the loop ----------------------------------------------------------------

def run(task, specialist="general", max_steps=None, approver=approve):
    specs = load_specialists()
    if specialist not in specs:
        return f"unknown specialist '{specialist}'. choose from: {', '.join(specs)}"
    spec = specs[specialist]
    allowed = spec["tools"]
    max_steps = max_steps or cfg.max_steps

    messages = [{"role": "system", "content": spec["prompt"] + memory_block(specialist)}]

    if spec.get("plan"):
        plan = make_plan(spec["prompt"], task)
        print(f"Plan:\n{plan}\n")
        messages.append({"role": "user", "content": f"Task: {task}\n\nPlan:\n{plan}\n\nExecute it, one tool call at a time."})
    else:
        messages.append({"role": "user", "content": task})

    schemas = SCHEMAS_FOR(allowed)
    reflected = False
    final = "hit max steps"

    for _ in range(max_steps):
        reply = ollama.chat(model=cfg.planning_model, messages=messages,
                            tools=schemas, think=False, keep_alive=cfg.ollama_keep_alive)
        msg = reply["message"]
        messages.append(msg)

        if not msg.get("tool_calls"):
            answer = msg["content"].strip()
            if not reflected:  # Reflect/Critic: check the goal once before quitting
                reflected = True
                verdict = reflect(task, answer)
                if not verdict.lower().startswith("yes"):
                    print(f"  reflect: {verdict}")
                    messages.append({"role": "user", "content": f"Not fully done: {verdict} Keep going."})
                    continue
            record(specialist, "successes", f"{task} -> {answer[:120]}")
            final = answer
            break

        for call in msg["tool_calls"]:
            name = call["function"]["name"]
            args = call["function"].get("arguments", {})

            if name not in allowed or name not in REGISTRY:
                result = f"error: tool '{name}' not available to this specialist"
                messages.append({"role": "tool", "tool_name": name, "content": result})
                continue

            entry = REGISTRY[name]
            print(f"  -> {name}({json.dumps(args)})")

            if entry["dangerous"] and not approver(name, args):
                result = "error: user declined this action"
                record(specialist, "failures", f"declined {name}({json.dumps(args)})")
                messages.append({"role": "tool", "tool_name": name, "content": result})
                continue

            try:
                result = entry["fn"](**args)
            except Exception as e:
                result = f"error: {e}"

            result = str(result)
            if result.startswith("error:") or result.startswith("ERROR"):
                record(specialist, "failures", f"{name}: {result[:120]}")
            elif entry["verify"]:  # eval gate: confirm it actually landed
                problem = entry["verify"](**args)
                if problem:
                    result = f"error: action ran but verification failed: {problem}"
                    record(specialist, "failures", f"{name}: {problem}")

            print(f"     {result[:120]}")
            messages.append({"role": "tool", "tool_name": name, "content": result})

    cleanup_playwright()
    return final


# --- CLI + self-check --------------------------------------------------------

def _parse_argv(argv):
    specialist = "general"
    if "--as" in argv:
        i = argv.index("--as")
        specialist = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    return specialist, " ".join(argv)


def demo():
    specs = load_specialists()
    assert {"general", "web-researcher", "google-ads", "insta-analyzer"} <= set(specs)
    for name, spec in specs.items():
        assert spec["tools"], name
        for t in spec["tools"]:
            assert t in REGISTRY, f"{name}: unknown tool {t}"
        assert len(SCHEMAS_FOR(spec["tools"])) == len(spec["tools"])

    assert _parse_argv(["--as", "google-ads", "hello", "world"]) == ("google-ads", "hello world")
    assert _parse_argv(["just", "a", "task"]) == ("general", "just a task")

    global MEM_DIR
    saved, MEM_DIR = MEM_DIR, ROOT / "memory_demo"
    try:
        shutil.rmtree(MEM_DIR, ignore_errors=True)
        assert memory_block("general") == ""
        record("general", "failures", "open_app Xyz: not found")
        record("general", "failures", "open_app Xyz: not found")  # dedup
        assert load_memory("general")["failures"] == ["open_app Xyz: not found"]
        assert "Past failures" in memory_block("general")
    finally:
        shutil.rmtree(MEM_DIR, ignore_errors=True)
        MEM_DIR = saved

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
        spec, task = _parse_argv(argv)
        if not task:
            print("give a task, e.g. python agent.py --as web-researcher \"...\"")
        else:
            print(run(task, spec))
    else:
        print(f"Usage: python agent.py --as <specialist> \"<task>\"")
        print(f"Specialists: {', '.join(load_specialists())}")

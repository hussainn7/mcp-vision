"""
A local Mac agent that gets things done by calling tools, not by clicking.

The model (qwen3:8b, running on the Metal GPU via Ollama) never touches the
mouse. It picks a tool and fills in the arguments; the tool is AppleScript or a
shell command that does the real work. That's why a small local model can be
reliable here: it only has to decide *what*, the OS handles *where*.

    python mac_agent.py "make a note called Ideas with a haiku about the sea"
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import ollama

from config import cfg

# where learned failures persist between runs
FAIL_LOG = Path(__file__).with_name("failures.json")

# a freshly-launched app often refuses the first AppleEvent; retry once
COLD_ERRORS = ("-609", "-1712", "-1708", "Connection is invalid")


def osa(script, *args):
    """Run an AppleScript. Arguments go through argv so we never escape quotes
    into the script. A cold app can be slow to reply, so give it a long timeout."""
    full = f"on run argv\nwith timeout of 120 seconds\n{script}\nend timeout\nend run"
    for attempt in range(2):
        out = subprocess.run(
            ["osascript", "-e", full, *[str(a) for a in args]],
            capture_output=True, text=True,
        )
        if out.returncode == 0:
            return out.stdout.strip() or "done"
        if attempt == 0 and any(c in out.stderr for c in COLD_ERRORS):
            time.sleep(1.5)
            continue
        return f"error: {out.stderr.strip()}"


def ensure(app):
    """Launch an app through LaunchServices and wait until its process is up,
    so the first AppleEvent doesn't hit a half-started app."""
    subprocess.run(["open", "-a", app], check=False)
    for _ in range(20):
        if subprocess.run(["pgrep", "-x", app], capture_output=True).returncode == 0:
            return
        time.sleep(0.3)


def create_note(title, body):
    ensure("Notes")
    return osa(
        'tell application "Notes" to make new note '
        'with properties {name:(item 1 of argv), body:(item 2 of argv)}',
        title, body,
    )


def add_reminder(text, due=""):
    ensure("Reminders")
    props = "{name:(item 1 of argv)}"
    if due:
        props = '{name:(item 1 of argv), due date:(date (item 2 of argv))}'
    return osa(f'tell application "Reminders" to make new reminder with properties {props}', text, due)


def create_event(title, when, calendar="Home"):
    ensure("Calendar")
    return osa(
        'tell application "Calendar" to tell calendar (item 3 of argv)\n'
        '  set d to date (item 2 of argv)\n'
        '  make new event with properties {summary:(item 1 of argv), start date:d, end date:(d + 3600)}\n'
        'end tell',
        title, when, calendar,
    )


def open_app(name):
    subprocess.run(["open", "-a", name], check=False)
    return f"opened {name}"


def list_dir(path="~"):
    out = subprocess.run(["ls", "-la", os.path.expanduser(path)],
                         capture_output=True, text=True)
    return out.stdout or out.stderr


def read_file(path):
    try:
        return open(os.path.expanduser(path)).read()[:4000]
    except OSError as e:
        return f"error: {e}"


TOOLS = {
    "create_note": create_note,
    "add_reminder": add_reminder,
    "create_event": create_event,
    "open_app": open_app,
    "list_dir": list_dir,
    "read_file": read_file,
}

SCHEMAS = [
    {"type": "function", "function": {
        "name": "create_note", "description": "Create a note in Apple Notes.",
        "parameters": {"type": "object", "required": ["title", "body"], "properties": {
            "title": {"type": "string"}, "body": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "add_reminder", "description": "Add a reminder. due is optional, like 'August 1, 2026 3:00 PM'.",
        "parameters": {"type": "object", "required": ["text"], "properties": {
            "text": {"type": "string"}, "due": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "create_event", "description": "Add a Calendar event. when is like 'August 1, 2026 3:00 PM'.",
        "parameters": {"type": "object", "required": ["title", "when"], "properties": {
            "title": {"type": "string"}, "when": {"type": "string"}, "calendar": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "open_app", "description": "Launch or focus a Mac app by name.",
        "parameters": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "list_dir", "description": "List files in a folder.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a text file's contents.",
        "parameters": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}}}},
]

# Eval gates: read the state back and confirm the action actually landed.
# A tool that "succeeded" can still have done nothing (blank note, wrong
# calendar), so we don't trust the return string — we check reality.
# verify(**args) returns "" when good, else a short problem description.

def verify_note(title, body):
    r = osa('tell application "Notes" to return (count of notes whose name is (item 1 of argv))', title)
    return "" if r.isdigit() and int(r) > 0 else f"note '{title}' not found after create"


def verify_reminder(text, due=""):
    r = osa('tell application "Reminders" to return (count of reminders whose name is (item 1 of argv))', text)
    return "" if r.isdigit() and int(r) > 0 else f"reminder '{text}' not found after add"


def verify_event(title, when, calendar="Home"):
    r = osa('tell application "Calendar" to tell calendar (item 2 of argv) to '
            'return (count of (events whose summary is (item 1 of argv)))', title, calendar)
    return "" if r.isdigit() and int(r) > 0 else f"event '{title}' not found in calendar '{calendar}'"


VERIFY = {
    "create_note": verify_note,
    "add_reminder": verify_reminder,
    "create_event": verify_event,
}


# Failure memory: every error or failed eval gate is appended here and fed
# back into the next run's prompt, so the agent stops repeating the same
# mistake across sessions. ponytail: flat JSON list, last 50 kept — swap for
# a real store only if this file ever gets big enough to matter.
def load_failures():
    try:
        return json.loads(FAIL_LOG.read_text())
    except (OSError, json.JSONDecodeError):
        return []


def record_failure(tool, args, error):
    fails = load_failures()
    entry = {"tool": tool, "args": args, "error": str(error)[:200]}
    if entry not in fails:
        fails.append(entry)
        FAIL_LOG.write_text(json.dumps(fails[-50:], indent=2))


def past_mistakes():
    fails = load_failures()
    if not fails:
        return ""
    lines = "\n".join(f"- {f['tool']}({json.dumps(f['args'])}) failed: {f['error']}" for f in fails[-10:])
    return f"\n\nLearn from these past failures and do not repeat them:\n{lines}"


SYSTEM = """You control a Mac by calling tools. Break the task into tool calls and
make them one at a time. When the task is fully done, reply in plain words with a
short confirmation and no more tool calls.

The home folder is {home}. Use ~ or that path for files; don't invent paths."""


def run(task, max_steps=8):
    messages = [{"role": "system", "content": SYSTEM.format(home=os.path.expanduser("~")) + past_mistakes()},
                {"role": "user", "content": task}]

    for _ in range(max_steps):
        reply = ollama.chat(
            model=cfg.planning_model,
            messages=messages,
            tools=SCHEMAS,
            think=False,
            keep_alive=cfg.ollama_keep_alive,
        )
        msg = reply["message"]
        messages.append(msg)

        if not msg.get("tool_calls"):
            return msg["content"].strip()

        for call in msg["tool_calls"]:
            name = call["function"]["name"]
            args = call["function"]["arguments"]
            print(f"  -> {name}({json.dumps(args)})")
            result = TOOLS[name](**args) if name in TOOLS else f"unknown tool {name}"

            # eval gate: confirm the action actually landed, else turn it into
            # an error the model has to react to
            if isinstance(result, str) and result.startswith("error:"):
                record_failure(name, args, result)
            elif name in VERIFY:
                problem = VERIFY[name](**args)
                if problem:
                    result = f"error: action ran but verification failed: {problem}"
                    record_failure(name, args, problem)

            print(f"     {result[:120]}")
            messages.append({"role": "tool", "tool_name": name, "content": result})

    return "hit max steps"


def demo():
    assert osa('return (item 1 of argv)', "ok") == "ok"
    assert osa('return ((item 1 of argv) & (item 2 of argv))', "a", "b") == "ab"
    assert "error:" in osa("this is not applescript")
    assert set(t["function"]["name"] for t in SCHEMAS) == set(TOOLS)
    assert set(VERIFY) <= set(TOOLS)  # every gate maps to a real tool

    # failure memory round-trips to disk and surfaces in the prompt
    global FAIL_LOG
    saved, FAIL_LOG = FAIL_LOG, Path(__file__).with_name("failures.demo.json")
    try:
        FAIL_LOG.unlink(missing_ok=True)
        assert past_mistakes() == ""
        record_failure("create_event", {"calendar": "Home"}, "calendar not found")
        assert "calendar not found" in past_mistakes()
        record_failure("create_event", {"calendar": "Home"}, "calendar not found")
        assert len(load_failures()) == 1  # deduped
    finally:
        FAIL_LOG.unlink(missing_ok=True)
        FAIL_LOG = saved
    print("ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        demo()
    elif len(sys.argv) > 1:
        print(run(" ".join(sys.argv[1:])))
    else:
        print(__doc__)

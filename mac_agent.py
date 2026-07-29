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

import ollama

from config import cfg

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

SYSTEM = """You control a Mac by calling tools. Break the task into tool calls and
make them one at a time. When the task is fully done, reply in plain words with a
short confirmation and no more tool calls.

The home folder is {home}. Use ~ or that path for files; don't invent paths."""


def run(task, max_steps=8):
    messages = [{"role": "system", "content": SYSTEM.format(home=os.path.expanduser("~"))},
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
            print(f"     {result[:120]}")
            messages.append({"role": "tool", "tool_name": name, "content": result})

    return "hit max steps"


def demo():
    assert osa('return (item 1 of argv)', "ok") == "ok"
    assert osa('return ((item 1 of argv) & (item 2 of argv))', "a", "b") == "ab"
    assert "error:" in osa("this is not applescript")
    assert set(t["function"]["name"] for t in SCHEMAS) == set(TOOLS)
    print("ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        demo()
    elif len(sys.argv) > 1:
        print(run(" ".join(sys.argv[1:])))
    else:
        print(__doc__)

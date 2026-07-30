"""
A state machine based Mac agent that uses micro-agents to execute tasks reliably.
"""

import json
import os
import subprocess
import sys
import time

import ollama

from config import cfg

COLD_ERRORS = ("-609", "-1712", "-1708", "Connection is invalid")

def osa(script, *args):
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
    "done": lambda: "done",
}

SCHEMAS = [
    {"name": "create_note", "description": "Create a note in Apple Notes.", "parameters": {"title": "string", "body": "string"}},
    {"name": "add_reminder", "description": "Add a reminder. due is optional, like 'August 1, 2026 3:00 PM'.", "parameters": {"text": "string", "due": "string"}},
    {"name": "create_event", "description": "Add a Calendar event. when is like 'August 1, 2026 3:00 PM'.", "parameters": {"title": "string", "when": "string", "calendar": "string"}},
    {"name": "open_app", "description": "Launch or focus a Mac app by name.", "parameters": {"name": "string"}},
    {"name": "list_dir", "description": "List files in a folder.", "parameters": {"path": "string"}},
    {"name": "read_file", "description": "Read a text file's contents.", "parameters": {"path": "string"}},
    {"name": "done", "description": "Call this when the task is fully complete.", "parameters": {}},
]

def ask_llm_json(prompt, system_prompt=""):
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    
    for attempt in range(3):
        reply = ollama.chat(
            model=cfg.planning_model,
            messages=messages,
            format="json",
            keep_alive=cfg.ollama_keep_alive,
        )
        content = reply["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": f"Invalid JSON: {e}. Please fix the syntax error and return only valid JSON."})
            
    print("Failed to get valid JSON after 3 attempts.")
    return None

def ask_llm_text(prompt, system_prompt=""):
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    
    reply = ollama.chat(
        model=cfg.planning_model,
        messages=messages,
        keep_alive=cfg.ollama_keep_alive,
    )
    return reply["message"]["content"].strip()


SELECTOR_SYSTEM_PROMPT = f"""You are a tool selector. 
Based on the user's task and the current history, you must choose EXACTLY ONE tool to run next.
Output MUST be a JSON object with two keys: "tool_name" and "arguments".
"arguments" must be a dictionary matching the tool's parameters.
If the task is fully completed, choose the tool "done".

Available tools:
{json.dumps(SCHEMAS, indent=2)}

Example output:
{{
  "tool_name": "create_note",
  "arguments": {{
    "title": "My Note",
    "body": "Hello world"
  }}
}}
"""

def summarize_result(task, tool_name, result):
    prompt = f"Task: {task}\\nWe just ran tool '{tool_name}'.\\nResult:\\n{result}\\n\\nBriefly summarize this result in 1 sentence. Focus only on the facts."
    return ask_llm_text(prompt)

def run(task, max_steps=8):
    print(f"Starting task: {task}")
    history = f"Task: {task}\\n\\nHistory:\\n"
    
    for step in range(max_steps):
        print(f"\\n--- Step {step + 1} ---")
        prompt = f"{history}\\nBased on the history, what is the next step? Choose one tool and output JSON."
        selection = ask_llm_json(prompt, system_prompt=SELECTOR_SYSTEM_PROMPT)
        
        if not selection or "tool_name" not in selection:
            print("Error: Invalid tool selection from LLM.")
            break
            
        tool_name = selection["tool_name"]
        args = selection.get("arguments", {})
        
        print(f"  -> {tool_name}({json.dumps(args)})")
        
        if tool_name == "done":
            print("Task completed!")
            break
            
        if tool_name not in TOOLS:
            result = f"error: unknown tool {tool_name}"
        else:
            try:
                result = TOOLS[tool_name](**args)
            except Exception as e:
                result = f"error: {e}"
                
        print(f"     {str(result)[:120]}")
        
        summary = summarize_result(task, tool_name, str(result)[:1000])
        print(f"  -> Summary: {summary}")
        
        history += f"\\nRan tool: {tool_name} with args {json.dumps(args)}\nResult Summary: {summary}\\n"
        
    return "Finished execution loop."


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run(" ".join(sys.argv[1:]))
    else:
        print("Usage: python state_agent.py <task>")

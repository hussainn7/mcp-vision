"""Plip end to end: the real ``plip`` CLI in a subprocess, with a fake ``claude`` on PATH.

Everything between the command line and the model is real: settings and
prefs, engine discovery and the sign-in probe, the Claude Code subprocess
streaming protocol, the companion loop, actions and their follow-up turns,
and memory. Only the model's words are scripted.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

FAKE_CLAUDE = r'''
import json, os, re, sys
args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "email": "e2e@example.com",
                      "subscriptionType": "max"}))
    sys.exit(0)
assert "-p" in args and "--tools" in args, args
message = json.loads(sys.stdin.readline())
content = message["message"]["content"]
text = content[-1]["text"]
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as log:
    log.write(json.dumps({"text": text, "images": sum(1 for part in content if part["type"] == "image"),
                          "system": args[args.index("--system-prompt") + 1][:40]}) + "\n")

def say(*chunks):
    for chunk in chunks:
        print(json.dumps({"type": "stream_event", "event": {"type": "content_block_delta",
                          "delta": {"type": "text_delta", "text": chunk}}}), flush=True)
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "".join(chunks)}))

current = text.rsplit("the user said:", 1)[-1].lower() if "the user said:" in text else text.lower()
if "(action results" in text:
    found = re.search(r"1\. (\S+) in (\S+)", text)
    say("Found it: " + (found.group(1) if found else "nothing") + ". ", "It's in " + (found.group(2) if found else "?") + ".")
elif "find my lease" in current:
    say("Looking for it now. ", '[DO:search_files {"query": "lease", "kind": "pdf"}]')
elif "my email" in current:
    email = re.search(r"- email: (\S+)", text)
    say("Your email is " + (email.group(1) if email else "a mystery") + ".")
elif "two factor" in current:
    say("[STEPS:3] [PLAN: open settings | security | turn on two factor] ",
        "First, open your profile menu up top. [POINT:600,20:profile menu]")
else:
    say("Sure.")
'''


@pytest.fixture
def plip(tmp_path):
    home = tmp_path / "home"
    (home / "Documents" / "Apartment").mkdir(parents=True)
    (home / "Documents" / "Apartment" / "Lease-2026.pdf").write_bytes(b"%PDF-1.7")
    (home / "Downloads").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(f"#!{sys.executable}\n" + textwrap.dedent(FAKE_CLAUDE))
    fake.chmod(0o755)
    log = tmp_path / "claude-calls.jsonl"
    log.touch()
    screenshot = tmp_path / "screen.png"
    from PIL import Image
    Image.new("RGB", (1440, 900), "#1d1d22").save(screenshot)
    env = {key: value for key, value in os.environ.items()
           if not key.endswith("_API_KEY") and not key.startswith(("BUDDY_", "CLAUDE", "MCP_VISION"))}
    env.update(PATH=f"{bin_dir}{os.pathsep}{env.get('PATH', '')}", HOME=str(home), FAKE_CLAUDE_LOG=str(log),
               MCP_VISION_CONFIG_DIR=str(tmp_path / "config"), MCP_VISION_STATE_DIR=str(tmp_path / "state"),
               PYTHONPATH=f"{ROOT / 'src'}{os.pathsep}{ROOT}", BUDDY_ROUTER="rules", BUDDY_ENGINE="claude-code",
               PYTHONFAULTHANDLER="1", MCP_VISION_NO_ANALYTICS="1")

    def run(*args, stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
        command = [sys.executable, "-m", "mcp_vision.cli", "buddy", *args]
        with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, env=env, cwd=tmp_path) as proc:
            try:
                out, err = proc.communicate(stdin, timeout=90)
            except subprocess.TimeoutExpired:
                proc.send_signal(signal.SIGABRT)            # faulthandler prints every thread's stack
                out, err = proc.communicate()
                pytest.fail(f"plip {' '.join(args)} hung\n{out}\n{err[-6000:]}")
        done = subprocess.CompletedProcess(command, proc.returncode, out, err)
        if check:
            assert done.returncode == 0, done.stdout + done.stderr
        return done

    def calls() -> list[dict]:
        return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]

    run.calls = calls
    run.screenshot = str(screenshot)
    run.config = tmp_path / "config"
    run.state = tmp_path / "state"
    return run


def test_doctor_finds_the_signed_in_claude_plan(plip):
    out = plip("doctor", check=False).stdout
    assert "brain claude-code: ready: Signed in as e2e@example.com · max <- Plip thinks with this" in out


def test_ask_searches_files_then_answers_from_the_results(plip):
    result = json.loads(plip("ask", "--json", "--image", plip.screenshot, "find my lease").stdout)
    assert result["engine"] == "Claude" and result["state"] == "done" and result["turns"] == 2
    assert result["did"] == ["Searching files for lease"]
    assert result["spoken"] == "Found it: Lease-2026.pdf. It's in ~/Documents/Apartment."
    first, second = plip.calls()
    assert first["images"] == 1 and "the user said: find my lease" in first["text"]
    assert first["system"].startswith("you're plip")
    assert second["images"] == 0 and "1. Lease-2026.pdf in ~/Documents/Apartment" in second["text"]


def test_memory_import_then_personal_answer(plip, tmp_path):
    memory_file = tmp_path / "chatgpt.txt"
    memory_file.write_text("Here's what I know:\n- Name: Ada Lovelace\n- Email: ada@example.com\n- Loves tea\n")
    assert "Imported 3 from chatgpt (3 new)." in plip("memory", "import", "chatgpt", "--file", str(memory_file)).stdout
    shown = plip("memory", "show").stdout
    assert "ada@example.com" in shown and "(ChatGPT)" in shown
    assert plip("ask", "what's my email").stdout.splitlines()[0] == "Your email is ada@example.com."
    assert "- email: ada@example.com" in plip.calls()[-1]["text"]
    assert "key: value" in plip("memory", "prompt").stdout


def test_how_to_questions_get_a_plan_and_a_point(plip):
    result = json.loads(plip("ask", "--json", "--image", plip.screenshot, "how do I turn on two factor").stdout)
    assert result["plan"] == ["open settings", "security", "turn on two factor"]
    assert result["spoken"] == "First, open your profile menu up top."
    assert result["targets"][0]["label"] == "profile menu"


def test_ask_logs_one_usage_row_and_reports_it(plip):
    result = json.loads(plip("ask", "--json", "--image", plip.screenshot, "find my lease").stdout)
    assert result["outcome"] == "done" and result["usage"]["estimated"] is True   # the fake reports no tokens
    rows = [json.loads(line) for line in (plip.state / "usage.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["kind"] == "cli" and rows[0]["turns"] == 2
    assert rows[0]["engine"] == "claude-code" and rows[0]["actions"] == ["search_files"]

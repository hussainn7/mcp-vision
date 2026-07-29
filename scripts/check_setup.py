"""
Pre-flight check. Run before your first agent task.

    python scripts/check_setup.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import cfg

fails = []


def check(name, fn):
    try:
        print(f"  [OK] {name} -- {fn()}")
    except Exception as e:
        print(f"  [FAIL] {name}: {e}")
        fails.append(name)


def python_version():
    assert sys.version_info >= (3, 12), f"need 3.12+, got {sys.version.split()[0]}"
    return sys.version.split()[0]


def imports():
    for mod in ("ollama", "mss", "PIL", "pyautogui"):
        __import__(mod)
    return "ollama, mss, PIL, pyautogui"


def model_present():
    import ollama

    listed = ollama.list()
    names = [m.model for m in getattr(listed, "models", [])]
    # mac_agent needs the tool-calling model; the vision one is only for simple_agent
    assert cfg.planning_model in names, (
        f"{cfg.planning_model} not pulled. Run: ollama pull {cfg.planning_model}\n"
        f"           Have: {', '.join(names) or 'nothing'}"
    )
    return cfg.planning_model


def applescript():
    import subprocess

    out = subprocess.run(["osascript", "-e", 'return "ok"'], capture_output=True, text=True)
    assert out.stdout.strip() == "ok", out.stderr.strip()
    return "osascript reachable (apps prompt for Automation on first use)"


print("\nChecking...")
check("python >= 3.12", python_version)
check("imports", imports)
check("ollama model", model_present)
check("applescript", applescript)

print()
if fails:
    print(f"{len(fails)} problem(s): {', '.join(fails)}")
    sys.exit(1)
print('Good to go.  python mac_agent.py "make a note called Ideas with a haiku"')

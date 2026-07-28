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
    for mod in ("mss", "PIL", "pyautogui", "ollama"):
        __import__(mod)
    return "mss, PIL, pyautogui, ollama"


def model_present():
    import ollama

    listed = ollama.list()
    names = [m.model for m in getattr(listed, "models", [])]
    assert cfg.model in names, (
        f"{cfg.model} not pulled. Run: ollama pull {cfg.model}\n"
        f"           Have: {', '.join(names) or 'nothing'}"
    )
    return cfg.model


def screen_capture():
    from phase1_vision.capture import capture_screen

    img, _ = capture_screen(save=False)
    # An all-black grab means Screen Recording permission was never granted.
    assert img.convert("L").getextrema() != (0, 0), (
        "screenshot is all black -- grant Screen Recording in "
        "System Settings > Privacy & Security"
    )
    return f"{img.width}x{img.height} logical points"


print("\nChecking...")
check("python >= 3.12", python_version)
check("imports", imports)
check("ollama model", model_present)
check("screen capture", screen_capture)

print()
if fails:
    print(f"{len(fails)} problem(s): {', '.join(fails)}")
    sys.exit(1)
print('Good to go.  python simple_agent.py "your task here"')

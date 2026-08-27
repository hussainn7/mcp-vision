"""Run every module self-check. CI-safe: no Ollama, no Chrome, no Mac.

    python tests/run.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# --demo for CLIs that otherwise wait for a task; others run demo() on __main__.
CHECKS = [
    [sys.executable, "trace.py"],
    [sys.executable, "trace_viewer.py", "--demo"],
    [sys.executable, "judge.py"],
    [sys.executable, "skills.py"],
    [sys.executable, "backends.py"],
    [sys.executable, "phase2_mcp/playwright_tools.py"],
    [sys.executable, "phase2_mcp/page_snapshot.py"],
    [sys.executable, "phase2_mcp/micro_vision.py"],
    [sys.executable, "phase2_mcp/client.py"],
    [sys.executable, "tools.py"],
    [sys.executable, "agent.py", "--demo"],
    [sys.executable, "runtime.py"],
    [sys.executable, "dashboard.py"],
    [sys.executable, "phase1_vision/coords.py"],
    [sys.executable, "phase1_vision/compress.py"],
]

# Pillow-backed perception. Skipped only if PIL is missing (stripped CI).
PILLOW = [
    [sys.executable, "phase1_vision/som.py"],
    [sys.executable, "phase1_vision/grounding.py"],
    [sys.executable, "phase1_vision/diff.py"],
]


def _has(mod):
    try:
        __import__(mod)
        return True
    except ImportError:
        return False


def main():
    checks = list(CHECKS)
    if _has("PIL"):
        checks.extend(PILLOW)
    if _has("pyautogui"):
        checks.append([sys.executable, "simple_agent.py", "--demo"])
    failed = []
    for cmd in checks:
        print(f"$ {' '.join(cmd)}")
        r = subprocess.run(cmd, cwd=ROOT)
        if r.returncode != 0:
            failed.append(" ".join(cmd))
    if failed:
        print(f"\n{len(failed)} failed:")
        for f in failed:
            print(f"  {f}")
        sys.exit(1)
    print(f"\n{len(checks)} checks passed")


if __name__ == "__main__":
    main()

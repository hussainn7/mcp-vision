#!/usr/bin/env python3
"""Build and exercise the installed wheel from outside the checkout."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run() -> int:
    with tempfile.TemporaryDirectory(prefix="mcp-vision-wheel-") as raw:
        temp = Path(raw)
        wheel_dir = temp / "wheel"
        wheel_dir.mkdir()
        if importlib.util.find_spec("build"):
            build = [sys.executable, "-m", "build", "--wheel", "--outdir", str(wheel_dir)]
        elif shutil.which("uv"):
            build = ["uv", "build", "--wheel", "--out-dir", str(wheel_dir),
                     "--cache-dir", str(temp / "uv-cache")]
        else:
            raise SystemExit("package smoke requires the build module or uv")
        subprocess.run(build, cwd=ROOT, check=True)
        wheels = list(wheel_dir.glob("*.whl"))
        if len(wheels) != 1:
            raise SystemExit("wheel build did not produce exactly one artifact")
        site = temp / "site"
        if importlib.util.find_spec("pip"):
            install = [sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(site),
                       str(wheels[0])]
        elif shutil.which("uv"):
            install = ["uv", "pip", "install", "--python", sys.executable, "--no-deps",
                       "--target", str(site), "--cache-dir", str(temp / "uv-cache"), str(wheels[0])]
        else:
            raise SystemExit("package smoke requires pip or uv for wheel installation")
        subprocess.run(install, check=True)
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(site)
        subprocess.run([sys.executable, str(ROOT / "tests" / "wheel_smoke.py")],
                       cwd=temp, env=environment, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())

"""Preflight checks: display, accessibility, local backends. Never writes to stdout."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass

from mcp_vision.log import get_logger

log = get_logger("mcp_vision.doctor")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def _python() -> Check:
    v = sys.version_info
    ok = v >= (3, 12)
    return Check("python", ok, f"{v.major}.{v.minor}.{v.micro}" + ("" if ok else " (need 3.12+)"))


def _mss() -> Check:
    try:
        import mss
        with mss.MSS() as sct:
            n = max(0, len(sct.monitors) - 1)
        return Check("display", True, f"{n} monitor(s) via mss")
    except Exception as e:
        return Check("display", False, str(e))


def _screen_recording() -> Check:
    if sys.platform != "darwin":
        return Check("screen-recording", True, f"{sys.platform}: no TCC gate")
    try:
        from Quartz import CGPreflightScreenCaptureAccess
        ok = bool(CGPreflightScreenCaptureAccess())
        return Check("screen-recording", ok, "granted" if ok else "enable in System Settings → Privacy → Screen Recording")
    except Exception as e:
        return Check("screen-recording", True, f"could not probe TCC ({e})")


def _accessibility() -> Check:
    if sys.platform != "darwin":
        return Check("accessibility", True, f"{sys.platform}: no AX gate")
    try:
        from ApplicationServices import AXIsProcessTrusted
        ok = bool(AXIsProcessTrusted())
        return Check("accessibility", ok, "granted" if ok else "enable in System Settings → Privacy → Accessibility")
    except Exception as e:
        return Check("accessibility", True, f"could not probe AX ({e})")


def _ollama() -> Check:
    try:
        import urllib.request
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1)
        return Check("ollama", True, "reachable at :11434")
    except Exception:
        return Check("ollama", False, "not reachable (optional for MCP tools)")


def _fastmcp() -> Check:
    try:
        import fastmcp  # noqa: F401
        return Check("fastmcp", True, "installed")
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP  # noqa: F401
            return Check("fastmcp", True, "mcp.server.fastmcp")
        except ImportError:
            return Check("fastmcp", False, "pip install fastmcp")


def _hud() -> Check:
    try:
        import PySide6  # noqa: F401
        return Check("hud", True, "PySide6 available")
    except ImportError:
        return Check("hud", True, "headless fallback (pip install 'mcp-vision[hud]')")


def _which() -> Check:
    exe = shutil.which("mcp-vision")
    return Check("cli", bool(exe), exe or "mcp-vision not on PATH (pipx install mcp-vision)")


def run_doctor() -> list[Check]:
    checks = [_python(), _fastmcp(), _mss(), _screen_recording(), _accessibility(), _hud(), _ollama(), _which()]
    for c in checks:
        log.info("%s %s — %s", "ok" if c.ok else "FAIL", c.name, c.detail)
    return checks

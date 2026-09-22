"""Permission diagnostics evaluated by the process that performs native actions."""
from __future__ import annotations

import os
import sys
from typing import Any


def native_permission_snapshot() -> dict[str, Any]:
    """Return non-secret identity and TCC state for the current process."""
    snapshot: dict[str, Any] = {
        "platform": sys.platform,
        "pid": os.getpid(),
        "process": "MCP-Vision" if sys.platform == "darwin" else os.path.basename(sys.executable),
        "bundleId": None,
        "bundlePath": None,
        "executablePath": sys.executable,
        "accessibility": True,
        "screenRecording": True,
        "scope": "current-process",
    }
    if sys.platform != "darwin":
        return snapshot
    try:
        from Foundation import NSBundle

        bundle = NSBundle.mainBundle()
        snapshot.update({
            "process": str(bundle.objectForInfoDictionaryKey_("CFBundleName") or "MCP-Vision"),
            "bundleId": str(bundle.bundleIdentifier() or "") or None,
            "bundlePath": str(bundle.bundlePath() or "") or None,
            "executablePath": str(bundle.executablePath() or sys.executable),
        })
    except Exception:
        pass
    try:
        import ApplicationServices as AX

        snapshot["accessibility"] = bool(AX.AXIsProcessTrusted())
    except Exception:
        snapshot["accessibility"] = None
    try:
        from Quartz import CGPreflightScreenCaptureAccess

        snapshot["screenRecording"] = bool(CGPreflightScreenCaptureAccess())
    except Exception:
        snapshot["screenRecording"] = None
    return snapshot


def request_screen_recording() -> dict[str, Any]:
    """Ask macOS for screen capture access after an explicit local UI action."""
    if sys.platform == "darwin":
        from Quartz import CGRequestScreenCaptureAccess

        CGRequestScreenCaptureAccess()
    return native_permission_snapshot()

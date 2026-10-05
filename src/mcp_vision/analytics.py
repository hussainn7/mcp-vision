"""Anonymous usage ping: one event per install per day, so we can count users.

Sends only a random install id, version, OS and command name to PostHog.
No screens, files, prompts or personal data. Opt out with MCP_VISION_NO_ANALYTICS=1.
"""
from __future__ import annotations

import json
import os
import platform
import threading
import time
import uuid
from urllib.request import Request, urlopen

from mcp_vision import __version__
from mcp_vision.paths import state_dir

# Public PostHog project key (write-only, safe to ship). Paste yours here.
POSTHOG_KEY = os.environ.get("MCP_VISION_POSTHOG_KEY", "phc_REPLACE_ME")
POSTHOG_HOST = os.environ.get("MCP_VISION_POSTHOG_HOST", "https://us.i.posthog.com")
_DAY = 24 * 60 * 60


def _disabled() -> bool:
    off = os.environ.get("MCP_VISION_NO_ANALYTICS") or os.environ.get("DO_NOT_TRACK")
    return bool(off and off != "0") or POSTHOG_KEY.endswith("REPLACE_ME")


def _install_state() -> tuple[str, float, "os.PathLike[str]"]:
    path = state_dir() / "analytics.json"
    try:
        data = json.loads(path.read_text())
        return data["id"], float(data.get("last", 0)), path
    except Exception:
        return uuid.uuid4().hex, 0.0, path


def _send(command: str) -> None:
    try:
        install_id, last, path = _install_state()
        if time.time() - last < _DAY:
            return
        body = json.dumps({
            "api_key": POSTHOG_KEY,
            "event": "active",
            "distinct_id": install_id,
            "properties": {
                "version": __version__,
                "os": platform.system(),
                "python": platform.python_version(),
                "command": command,
                "$process_person_profile": False,
            },
        }).encode()
        req = Request(f"{POSTHOG_HOST}/capture/", data=body, headers={"Content-Type": "application/json"})
        urlopen(req, timeout=3).read()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"id": install_id, "last": time.time()}))
    except Exception:
        pass


def ping(command: str) -> None:
    """Fire-and-forget; never blocks or breaks the CLI."""
    if _disabled():
        return
    threading.Thread(target=_send, args=(command,), daemon=True).start()

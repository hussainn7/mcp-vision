"""Usage ping and setup funnel: so we can count users, and see where setup loses them.

The daily ping sends only a random install id, version, OS and command name to PostHog. The funnel events
(``track``) say which step of setup was reached: a permission granted, an AI connected, the first task done,
sign-in asked / done / skipped. They're keyed on the install id like the ping (one funnel per install, from the
first walkthrough step on) and carry the account id (see buddy/account.py) once there is one, so the funnel
lines up with the user list. No screens, files, prompts or personal data, ever.
Opt out with MCP_VISION_NO_ANALYTICS=1 (or DO_NOT_TRACK=1).
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
POSTHOG_KEY = os.environ.get("MCP_VISION_POSTHOG_KEY", "phc_vmiczR8czTXAXAcrHWGEpdkUq9X4aMjxwaxG7mFBhGcU")
POSTHOG_HOST = os.environ.get("MCP_VISION_POSTHOG_HOST", "https://us.i.posthog.com")
_DAY = 24 * 60 * 60
# The setup funnel, in order. Nothing else goes through track().
FUNNEL = ("onboarding_step", "permission_granted", "engine_connected", "first_task_done",
          "signin_prompted", "signin_done", "signin_skipped")


def _disabled() -> bool:
    off = os.environ.get("MCP_VISION_NO_ANALYTICS") or os.environ.get("DO_NOT_TRACK")
    return bool(off and off != "0") or not POSTHOG_KEY.startswith("phc_")


_state_lock = threading.Lock()                  # two events in the same second must not each invent an id


def _install_state() -> tuple[str, float, "os.PathLike[str]"]:
    """The install id and when the ping last went out. A new id is written down at once, so it stays put."""
    path = state_dir() / "analytics.json"
    with _state_lock:
        try:
            data = json.loads(path.read_text())
            return data["id"], float(data.get("last", 0)), path
        except Exception:
            install_id = uuid.uuid4().hex
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"id": install_id, "last": 0}))
            except OSError:
                pass
            return install_id, 0.0, path


def _post(event: str, distinct_id: str, properties: dict) -> bool:
    body = json.dumps({"api_key": POSTHOG_KEY, "event": event, "distinct_id": distinct_id,
                       "properties": {"version": __version__, "os": platform.system(),
                                      "os_version": platform.mac_ver()[0] or platform.release(),
                                      "$process_person_profile": False, **properties}}).encode()
    req = Request(f"{POSTHOG_HOST}/capture/", data=body, headers={"Content-Type": "application/json"})
    return urlopen(req, timeout=5).status == 200


def report_issue(message: str, engine: str = "") -> bool:
    """Sent only when the user presses Send report under Report a bug, so it ignores the usage opt-out."""
    return _report("issue_reported", {"message": message, "engine": engine})


def request_feature(message: str) -> bool:
    """Sent only when the user presses Send under Request a feature: what they typed, nothing else."""
    return _report("feature_request", {"message": message})


def _report(event: str, properties: dict) -> bool:
    if not POSTHOG_KEY.startswith("phc_"):
        return False
    try:
        install_id, _, _ = _install_state()
        return _post(event, install_id, properties)
    except Exception:
        return False


def _send(command: str) -> None:
    try:
        install_id, last, path = _install_state()
        if time.time() - last < _DAY:
            return
        _post("active", install_id, {"command": command, "python": platform.python_version()})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"id": install_id, "last": time.time()}))
    except Exception:
        pass


def ping(command: str) -> None:
    """Fire-and-forget; never blocks or breaks the CLI."""
    if _disabled():
        return
    threading.Thread(target=_send, args=(command,), daemon=True).start()


def _track(event: str, properties: dict) -> None:
    try:
        install_id, _, _ = _install_state()
        _post(event, install_id, properties)
    except Exception:
        pass


def track(event: str, properties: dict | None = None) -> bool:
    """A setup funnel event, keyed on the install id like the ping (``account`` rides along as a property).

    Fire-and-forget, honours the opt-out, and refuses anything outside FUNNEL. True when it was sent off.
    """
    if _disabled() or event not in FUNNEL:
        return False
    threading.Thread(target=_track, args=(event, dict(properties or {})), daemon=True).start()
    return True

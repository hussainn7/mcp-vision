"""User-facing challenge pause (CAPTCHA etc.). Never auto-solves."""
from __future__ import annotations

import math
import subprocess
import sys
import time

from phase2_mcp.auth_detector import AuthChallenge, detect_auth_challenge

_forced: bool | None = None


def set_forced_challenge_result(value: bool | None) -> None:
    global _forced
    _forced = value


def _notify(title: str, body: str) -> None:
    if sys.platform != "darwin":
        print(f"[{title}] {body}", file=sys.stderr)
        return
    t = title.replace("\\", "\\\\").replace('"', '\\"')[:80]
    b = body.replace("\\", "\\\\").replace('"', '\\"')[:180]
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{b}" with title "{t}" sound name "Sosumi"'],
            capture_output=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


_DIALOG = '''on run argv
    activate
    set answer to display dialog (item 1 of argv) with title "MCP-Vision needs you" buttons {"Abort", "I solved it"} default button "I solved it" cancel button "Abort" with icon caution giving up after (item 2 of argv as integer)
    if gave up of answer then return "timeout"
    if button returned of answer is "I solved it" then return "ok"
    return "abort"
end run'''


def wait_for_user_challenge(challenge: AuthChallenge, timeout_s: float = 300.0) -> bool:
    """Tell the user to solve the challenge in Chrome; wait for confirmation."""
    if _forced is not None:
        return _forced

    kind = "CAPTCHA" if challenge.challenge_type == "captcha" else challenge.challenge_type.replace("_", " ")
    prompt = (
        f"{challenge.service} {kind} is blocking the page.\n\n"
        f"{challenge.url[:120]}\n\n"
        "Solve it in Chrome yourself. I will not try to bypass it.\n"
        "Click “I solved it” when the page is usable again."
    )
    _notify("MCP-Vision: solve this in Chrome", f"{challenge.service} {kind}")
    print(challenge.format_banner(), file=sys.stderr)

    if sys.platform == "darwin":
        timeout = max(30, min(600, math.ceil(timeout_s)))
        try:
            result = subprocess.run(
                ["osascript", "-e", _DIALOG, prompt, str(timeout)],
                capture_output=True, text=True, timeout=timeout + 5,
            )
            return result.returncode == 0 and result.stdout.strip() == "ok"
        except (OSError, subprocess.TimeoutExpired):
            return False

    if not sys.stdin.isatty():
        # Headless hosts: wait a bit for the human at the real Chrome window.
        deadline = time.time() + min(timeout_s, 120)
        while time.time() < deadline:
            time.sleep(2)
        return False

    try:
        line = input(">> Press Enter after you solve it (or 'abort'): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return line not in {"abort", "cancel", "q", "quit", "no"}


def page_has_captcha(url: str = "", title: str = "", text: str = "", elements=None) -> AuthChallenge | None:
    challenge = detect_auth_challenge(url=url, title=title, text=text, elements=elements)
    if challenge and challenge.challenge_type == "captcha":
        return challenge
    return None

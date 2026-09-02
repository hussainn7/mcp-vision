"""Authentication challenge detector and first-class AUTH_REQUIRED state handler.

Detects sign-in walls, 2FA prompts, SSO redirects, and CAPTCHAs, then pauses
agent execution cleanly so the human user can authenticate before resuming.
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from mcp_vision.log import get_logger

logger = get_logger("mcp_vision.auth")


@dataclass
class AuthChallenge:
    service: str
    challenge_type: str  # "sign_in", "2fa", "captcha", "password", "sso"
    url: str
    title: str
    prompt_message: str

    def format_banner(self) -> str:
        return (
            "\n"
            + "=" * 78
            + f"\n[AUTH_REQUIRED] {self.service} {self.challenge_type.replace('_', ' ').title()} Challenge\n"
            + f"URL: {self.url}\n"
            + f"\n{self.prompt_message}\n"
            + "\nI've paused the task. Sign in / complete verification in the browser window."
            + "\n[Press Enter when signed in to continue, or type 'abort' to cancel]\n"
            + "=" * 78
            + "\n"
        )


_AUTH_URL_PATTERNS = [
    (re.compile(r"accounts\.google\.com", re.I), "Google", "sign_in"),
    (re.compile(r"github\.com/(?:login|session)", re.I), "GitHub", "sign_in"),
    (re.compile(r"linkedin\.com/(?:login|checkpoint|uas/login)", re.I), "LinkedIn", "sign_in"),
    (re.compile(r"login\.microsoftonline\.com|login\.live\.com", re.I), "Microsoft", "sign_in"),
    (re.compile(r"slack\.com/signin", re.I), "Slack", "sign_in"),
    (re.compile(r"amazon\.[a-z\.]+/ap/signin", re.I), "Amazon", "sign_in"),
    (re.compile(r"challenges\.cloudflare\.com", re.I), "Cloudflare", "captcha"),
    (re.compile(r"appleid\.apple\.com/auth/authorize", re.I), "Apple", "sign_in"),
    (re.compile(r"twitter\.com/i/flow/login|x\.com/i/flow/login", re.I), "X / Twitter", "sign_in"),
]

_AUTH_TEXT_PATTERNS = [
    (re.compile(r"sign in (?:to continue|with your google|to gmail)", re.I), "Google", "sign_in"),
    (re.compile(r"two-factor authentication|enter verification code|2-step verification", re.I), "Security", "2fa"),
    (re.compile(r"verify you are human|checking your browser|hcaptcha|recaptcha|security check", re.I), "Bot Detection", "captcha"),
    (re.compile(r"sign in to (?:github|linkedin|slack|amazon|your account)", re.I), "Service", "sign_in"),
    (re.compile(r"please sign in|please log in|enter your password|username and password", re.I), "Service", "password"),
]


def detect_auth_challenge(
    url: str = "",
    title: str = "",
    text: str = "",
    elements: Optional[List[Dict[str, Any]]] = None,
) -> Optional[AuthChallenge]:
    """Inspect URL, title, snapshot text, and UI elements to detect auth walls."""
    combined_text = f"{title}\n{text}".lower()
    combined_url = url or ""

    # 1. URL pattern check
    for pat, service, ctype in _AUTH_URL_PATTERNS:
        if pat.search(combined_url):
            # refine 2fa vs sign_in
            if any(k in combined_text for k in ("2fa", "two-step", "two-factor", "2-step", "verification", "authenticator")):
                ctype = "2fa"
            elif "captcha" in combined_text or "challenge" in combined_url:
                ctype = "captcha"

            msg = f"{service} authentication required at {combined_url[:60]}."
            return AuthChallenge(
                service=service,
                challenge_type=ctype,
                url=combined_url,
                title=title,
                prompt_message=msg,
            )

    # 2. Text pattern check on page content
    for pat, service, ctype in _AUTH_TEXT_PATTERNS:
        if pat.search(combined_text):
            # avoid false positives on search results or articles that mention "sign in"
            # check if URL or elements indicate actual login form
            is_login_page = any(
                k in combined_url.lower() for k in ("login", "signin", "auth", "sso", "session", "challenge", "checkpoint", "verify")
            )
            has_auth_el = False
            if elements:
                for el in elements:
                    label = str(el.get("label") or el.get("name") or "").lower()
                    role = str(el.get("role") or "").lower()
                    if "password" in label or role == "password" or label in ("sign in", "log in", "verify"):
                        has_auth_el = True
                        break

            if is_login_page or has_auth_el:
                msg = f"{service} {ctype.replace('_', ' ')} required. Please log in to continue."
                return AuthChallenge(
                    service=service,
                    challenge_type=ctype,
                    url=combined_url,
                    title=title,
                    prompt_message=msg,
                )

    return None


_forced_auth_result: Optional[bool] = None


def set_forced_auth_result(value: Optional[bool]) -> None:
    """For unit testing: force auth completion result without blocking."""
    global _forced_auth_result
    _forced_auth_result = value


def handle_auth_pause(
    challenge: AuthChallenge,
    page: Any = None,
    confirmer: Optional[Callable[[str], bool]] = None,
    timeout_s: float = 300.0,
) -> bool:
    """Handle first-class AUTH_REQUIRED state: notify user, pause, wait for auth, and verify."""
    global _forced_auth_result
    if _forced_auth_result is not None:
        logger.info(f"AUTH_REQUIRED: using forced result {_forced_auth_result}")
        return _forced_auth_result

    print(challenge.format_banner(), file=sys.stderr)

    if confirmer:
        return confirmer(challenge.prompt_message)

    if not sys.stdin.isatty():
        logger.warning("AUTH_REQUIRED: non-interactive environment (no TTY); waiting up to 60s for external auth...")
        # In non-interactive mode with a real page, poll for URL change
        if page:
            orig_url = page.url
            start = time.time()
            while time.time() - start < 60:
                time.sleep(2.0)
                try:
                    if page.url != orig_url and not detect_auth_challenge(page.url, page.title(), ""):
                        logger.info("AUTH_REQUIRED: detected URL change away from login page!")
                        return True
                except Exception:
                    pass
        return False

    # Interactive TTY wait loop
    try:
        user_input = input(">> Press [Enter] once signed in (or 'abort' to cancel): ").strip().lower()
        if user_input in ("abort", "cancel", "no", "q", "quit"):
            print("Authentication aborted by user.", file=sys.stderr)
            return False
    except (EOFError, KeyboardInterrupt):
        return False

    # Verify if page moved after user confirmed sign-in
    if page:
        try:
            time.sleep(1.0)
            cur_url = page.url
            cur_title = page.title()
            cur_text = page.inner_text("body")[:1000] if hasattr(page, "inner_text") else ""
            recheck = detect_auth_challenge(cur_url, cur_title, cur_text)
            if recheck and recheck.service == challenge.service and cur_url == challenge.url:
                logger.warning("Still on auth page after confirmation; continuing anyway as requested.")
        except Exception:
            pass

    print("Authentication confirmed. Resuming task trajectory...", file=sys.stderr)
    return True


def demo():
    c1 = detect_auth_challenge(
        url="https://accounts.google.com/v3/signin/identifier",
        title="Sign in - Google Accounts",
        text="Sign in to continue to Gmail",
    )
    assert c1 is not None, "Failed to detect Google sign-in"
    assert c1.service == "Google" and c1.challenge_type == "sign_in"

    c2 = detect_auth_challenge(
        url="https://github.com/login",
        title="Sign in to GitHub · GitHub",
        text="Username or email address Password Sign in",
    )
    assert c2 is not None and c2.service == "GitHub"

    # Test non-auth page does not trigger false positive
    c3 = detect_auth_challenge(
        url="https://en.wikipedia.org/wiki/Mechanical_keyboard",
        title="Mechanical keyboard - Wikipedia",
        text="A mechanical keyboard is a computer keyboard...",
    )
    assert c3 is None, "False positive on Wikipedia article"

    # Test forced auth result
    set_forced_auth_result(True)
    assert handle_auth_pause(c1) is True
    set_forced_auth_result(None)

    print("auth_detector ok")


if __name__ == "__main__":
    demo()

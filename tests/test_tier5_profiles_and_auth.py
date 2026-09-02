"""Tier 5: Existing Chrome Profiles & Authentication First-Class State Tests."""

from pathlib import Path
import pytest

from phase2_mcp.chrome_profiles import (
    extract_profile_from_prompt,
    get_agent_chrome_user_data_dir,
    get_launch_user_data_dir,
    is_system_chrome_user_data_dir,
    list_profiles,
    resolve_profile,
)
from phase2_mcp.auth_detector import (
    AuthChallenge,
    detect_auth_challenge,
    handle_auth_pause,
    set_forced_auth_result,
)


def test_chrome_profile_prompt_extraction():
    assert extract_profile_from_prompt("Check my Gmail using my personal Chrome profile") == "personal"
    assert extract_profile_from_prompt("Search on GitHub in work profile") == "work"
    assert extract_profile_from_prompt("Open calendar with school profile") == "school"
    assert extract_profile_from_prompt("Just search for laptops") is None


def test_chrome_profile_resolution_fallback():
    # Test resolution with synthetic non-existent dir (returns query gracefully)
    dir_name, display = resolve_profile("work", user_data_dir=Path("/tmp/nonexistent_chrome_dir"))
    assert dir_name == "work"


def test_agent_profile_dir_not_system_chrome():
    system = Path.home() / "Library/Application Support/Google/Chrome"
    profile_dir, display, sub = get_launch_user_data_dir(str(system), "personal")
    assert not is_system_chrome_user_data_dir(profile_dir)
    assert profile_dir == get_agent_chrome_user_data_dir("personal")
    assert sub is None


def test_auth_detection_google_signin():
    challenge = detect_auth_challenge(
        url="https://accounts.google.com/v3/signin/identifier?continue=https://mail.google.com",
        title="Sign in - Google Accounts",
        text="Sign in to continue to Gmail. Email or phone. Forgot email?",
    )
    assert challenge is not None
    assert challenge.service == "Google"
    assert challenge.challenge_type == "sign_in"
    assert "Google" in challenge.format_banner()
    assert "[AUTH_REQUIRED]" in challenge.format_banner()


def test_auth_detection_github_2fa():
    challenge = detect_auth_challenge(
        url="https://github.com/sessions/two-factor",
        title="Two-factor authentication · GitHub",
        text="Two-factor authentication. Open your two-factor authenticator app.",
    )
    assert challenge is not None
    assert challenge.service == "GitHub"
    assert challenge.challenge_type == "2fa"


def test_auth_detection_cloudflare_captcha():
    challenge = detect_auth_challenge(
        url="https://challenges.cloudflare.com/turnstile/v0/api.js",
        title="Just a moment...",
        text="Verify you are human. Checking your browser before accessing the website.",
    )
    assert challenge is not None
    assert challenge.challenge_type == "captcha"


def test_auth_detection_non_auth_page():
    challenge = detect_auth_challenge(
        url="https://news.ycombinator.com",
        title="Hacker News",
        text="Hacker News new | past | comments | ask | show | jobs | submit",
    )
    assert challenge is None


def test_auth_pause_and_resume_forced():
    challenge = AuthChallenge(
        service="Google",
        challenge_type="sign_in",
        url="https://accounts.google.com/signin",
        title="Sign in",
        prompt_message="Sign in required",
    )

    set_forced_auth_result(True)
    assert handle_auth_pause(challenge) is True

    set_forced_auth_result(False)
    assert handle_auth_pause(challenge) is False

    set_forced_auth_result(None)

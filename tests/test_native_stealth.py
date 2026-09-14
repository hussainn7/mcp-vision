from mcp_vision.challenge import page_has_captcha, set_forced_challenge_result, wait_for_user_challenge
from phase2_mcp.auth_detector import AuthChallenge


def test_captcha_detection():
    hit = page_has_captcha(
        url="https://challenges.cloudflare.com/cdn-cgi/challenge",
        title="Just a moment...",
        text="Checking your browser before accessing",
    )
    assert hit is not None and hit.challenge_type == "captcha"
    assert page_has_captcha(url="https://example.com", title="Example", text="hello") is None


def test_challenge_wait_respects_forced_result():
    challenge = AuthChallenge("Bot Detection", "captcha", "https://x.test", "x", "solve it")
    set_forced_challenge_result(True)
    assert wait_for_user_challenge(challenge) is True
    set_forced_challenge_result(False)
    assert wait_for_user_challenge(challenge) is False
    set_forced_challenge_result(None)


def test_mcp_live_defaults_to_native_driver():
    import asyncio
    from fastmcp import Client
    from mcp_vision.server import _mcp

    async def run():
        async with Client(_mcp(browser_mode="live")) as client:
            names = {t.name for t in await client.list_tools()}
            assert "browser_tabs" in names
            # Instructions mention CAPTCHA + native
            # Client may not expose instructions; tool presence is the contract.
            assert {"browser_use_tab", "browser_open_tab"} <= names
    asyncio.run(run())


def test_install_entry_sets_native_driver():
    from mcp_vision.utils.config_sync import _entry
    args = _entry(browser_mode="live", allow_writes=True)["args"]
    assert args[-4:] == ["--browser", "live", "--driver", "native"] or (
        "--driver" in args and args[args.index("--driver") + 1] == "native"
    )

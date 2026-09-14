import asyncio
from unittest.mock import AsyncMock

import pytest

from mcp_vision.live_browser import LiveBrowserRuntime, local_endpoint
from mcp_vision.server import _mcp


@pytest.mark.parametrize("url", ["https://remote.test:9222", "ws://remote.test:9222/devtools/browser/x",
    "file:///tmp", "http://localhost", "http://user:pass@localhost:9222", "ws://127.0.0.1:9222/#x"])
def test_remote_endpoints_rejected(url):
    with pytest.raises(ValueError):
        local_endpoint(url)


def test_missing_connection_does_not_launch_browser(monkeypatch):
    monkeypatch.setattr("mcp_vision.live_browser.websocket_endpoint", lambda: None)
    runtime = LiveBrowserRuntime()
    result = asyncio.run(runtime.tabs())
    assert result["connected"] is False
    assert "chrome://inspect" in result["error"]
    assert runtime._playwright is None and runtime.page is None


def test_disconnect_never_closes_user_browser():
    runtime = LiveBrowserRuntime()
    browser, driver = AsyncMock(), AsyncMock()
    runtime._browser, runtime._playwright = browser, driver
    asyncio.run(runtime.close())
    browser.close.assert_not_called()
    driver.stop.assert_awaited_once()


def test_mcp_live_host_contract():
    async def run():
        from fastmcp import Client
        async with Client(_mcp(browser_mode="live")) as client:
            names = {t.name for t in await client.list_tools()}
            assert {"browser_tabs", "browser_use_tab", "browser_open_tab", "browser_snapshot", "screen_image"} <= names
    asyncio.run(run())

"""Two independent drivers, one Chrome profile; no personal browser data."""
import asyncio
import os

import pytest
from playwright.async_api import async_playwright

from mcp_vision.live_browser import LiveBrowserRuntime
from phase2_mcp.chrome_bridge import websocket_endpoint

pytestmark = pytest.mark.skipif(os.environ.get("MCP_VISION_BROWSER_TESTS") != "1", reason="browser tests opt-in")


def test_existing_tabs_cookies_actions_and_disconnect(tmp_path):
    async def run():
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(str(tmp_path), headless=True,
                                                               args=["--remote-debugging-port=0"])
            try:
                await context.route("https://live.test/**", lambda r: r.fulfill(content_type="text/html", body='''
                    <title>Existing work</title><label for="title">Draft title</label><input id="title">
                    <button onclick="document.getElementById('result').textContent='Preview ready'">Preview</button>
                    <form><button>Send</button></form><p id="result"></p>'''))
                page = context.pages[0]
                await page.goto("https://live.test/")
                await context.add_cookies([{"name": "session_test", "value": "kept", "url": "https://live.test/"}])
                runtime = LiveBrowserRuntime(endpoint=websocket_endpoint(tmp_path), allow_writes=True)
                try:
                    listed = await runtime.tabs()
                    assert listed["connected"] and len(listed["tabs"]) == 1
                    tab = listed["tabs"][0]
                    assert (await runtime.navigate("https://live.test/other")).status == "error"
                    assert page.url == "https://live.test/"
                    assert (await runtime.use_tab(tab["tab_id"], "https://wrong.test")).status == "stale"
                    assert (await runtime.use_tab(tab["tab_id"], tab["url"])).status == "verified"
                    assert await runtime.page.evaluate("document.cookie") == "session_test=kept"
                    assert (await runtime.act("fill", "Draft title", "textbox", "Real shared tab")).status == "verified"
                    assert await page.input_value("#title") == "Real shared tab"
                    assert (await runtime.act("click", "Preview", "button")).executed is True
                    assert (await runtime.verify_text("Preview ready")).status == "verified"
                    assert (await runtime.act("click", "Send", "button")).status == "blocked"
                    assert (await runtime.open_tab("https://live.test/new")).status == "verified"
                    assert len(context.pages) == 2 and page.url == "https://live.test/"
                finally:
                    await runtime.close()
                assert not page.is_closed()
                assert await page.input_value("#title") == "Real shared tab"
                assert (await context.cookies())[0]["value"] == "kept"
            finally:
                await context.close()
    asyncio.run(run())

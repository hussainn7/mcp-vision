"""Grounded tools in an operator-approved, already running Chrome session."""
from __future__ import annotations

import uuid
from urllib.parse import urlsplit

from mcp_vision.browser import BrowserRuntime, Receipt
from mcp_vision.redaction import redact
from phase2_mcp.chrome_bridge import websocket_endpoint


def local_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "ws"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or not parsed.port or parsed.fragment):
        raise ValueError("Chrome endpoint must be a loopback HTTP or WebSocket URL with a port.")
    return value


class LiveBrowserRuntime(BrowserRuntime):
    def __init__(self, *, endpoint=None, **options):
        super().__init__(**options)
        self.endpoint = local_endpoint(endpoint) if endpoint else None
        self._tabs = {}

    async def _connect(self):
        if self._browser is not None:
            if not self._browser.is_connected():
                raise RuntimeError("Chrome disconnected. Restart the MCP connection and select a tab again.")
            return
        endpoint = self.endpoint or websocket_endpoint()
        if not endpoint:
            raise RuntimeError("Chrome connection is not enabled. In Chrome 144+, open chrome://inspect/#remote-debugging, "
                               "enable Remote debugging, then allow this connection when Chrome asks. "
                               "MCP-Vision will not restart Chrome or change its settings.")
        from playwright.async_api import async_playwright
        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.connect_over_cdp(local_endpoint(endpoint), timeout=15000)
        except Exception:
            await self._playwright.stop()
            self._playwright = None
            raise

    async def _ensure(self):
        await self._connect()
        if self.page is None or self.page.is_closed():
            raise RuntimeError("No open tab selected. Call browser_tabs, then browser_use_tab with its tab_id and URL, "
                               "or browser_open_tab for a new tab in your existing Chrome profile.")

    async def tabs(self) -> dict:
        async with self._lock:
            try:
                await self._connect()
                pages = [p for c in self._browser.contexts for p in c.pages if not p.is_closed()]
                self._tabs = {key: p for key, p in self._tabs.items() if p in pages}
                result = []
                for page in pages:
                    try:
                        self._check_url(page.url)
                        title = await page.title()
                    except Exception:
                        continue
                    key = next((key for key, p in self._tabs.items() if p is page), None)
                    if key is None:
                        key = uuid.uuid4().hex[:12]
                        self._tabs[key] = page
                    result.append({"tab_id": key, "url": page.url, "title": title, "selected": page is self.page})
                return {"connected": True, "mode": "existing-chrome", "tabs": result}
            except Exception as exc:
                return {"connected": False, "mode": "existing-chrome", "tabs": [], "error": redact(str(exc))}

    async def use_tab(self, tab_id: str, expected_url: str) -> Receipt:
        async with self._lock:
            try:
                await self._connect()
                target = self._tabs.get(tab_id)
                if target is None or target.is_closed() or target.url != expected_url:
                    return Receipt(status="stale", action="use_tab", message="Tab closed or URL changed. List tabs again.")
                self._check_url(target.url)
                await self._invalidate()
                self.page = target
                return Receipt(status="verified", action="use_tab", executed=True,
                               message="Selected the existing tab; inspect before acting.",
                               evidence={"tab_id": tab_id, "url": target.url})
            except Exception as exc:
                return Receipt(status="error", action="use_tab", message=redact(str(exc)))

    async def open_tab(self, url: str) -> Receipt:
        async with self._lock:
            executed = False
            try:
                self._check_url(url)
                await self._connect()
                contexts = self._browser.contexts
                if not contexts:
                    raise RuntimeError("Chrome has no existing profile context. Open a Chrome window first.")
                context = self.page.context if self.page is not None and not self.page.is_closed() else contexts[0]
                await self._invalidate()
                executed = None
                self.page = await context.new_page()
                await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
                self._check_url(self.page.url)
                key = uuid.uuid4().hex[:12]
                self._tabs[key] = self.page
                return Receipt(status="verified", action="open_tab", executed=True,
                               message="Opened a tab in the existing Chrome profile.",
                               evidence={"tab_id": key, "url": self.page.url})
            except Exception as exc:
                return Receipt(status="error", action="open_tab", executed=executed, message=redact(str(exc)))

    async def close(self):
        async with self._lock:
            await self._invalidate()
            # Stop our driver only. Chrome and all user tabs belong to the user.
            if self._playwright:
                await self._playwright.stop()
            self._tabs.clear()
            self.page = self._browser = self._context = self._playwright = None

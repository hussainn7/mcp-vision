"""Pluggable low-level execution boundary.

MCP-Vision owns orchestration, trust, verification, receipts, and UX. Existing
browser runtimes remain the default executor; alternatives can implement this
protocol without creating another orchestration loop.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from mcp_vision.browser import BrowserSnapshot, Receipt


@runtime_checkable
class ExecutionBackend(Protocol):
    async def tabs(self) -> dict: ...
    async def use_tab(self, tab_id: str, expected_url: str) -> Receipt: ...
    async def open_tab(self, url: str) -> Receipt: ...
    async def navigate(self, url: str) -> Receipt: ...
    async def snapshot(self) -> BrowserSnapshot: ...
    async def act(self, action: str, name: str, role: str = "", text: str = "") -> Receipt: ...
    async def click(self, snapshot_id: str, index: int) -> Receipt: ...
    async def fill(self, snapshot_id: str, index: int, text: str) -> Receipt: ...
    async def select(self, snapshot_id: str, index: int, value: str) -> Receipt: ...
    async def set_checked(self, snapshot_id: str, index: int, checked: bool) -> Receipt: ...
    async def upload(self, snapshot_id: str, index: int, path: str) -> Receipt: ...
    async def scroll(self, snapshot_id: str, delta_y: int) -> Receipt: ...
    async def verify_text(self, text: str) -> Receipt: ...
    async def screenshot(self) -> bytes: ...
    async def close(self) -> None: ...


def create_execution_backend(*, browser_mode: str, live_driver: str = "native",
                             cdp_endpoint: str | None = None, **options: Any) -> ExecutionBackend:
    """Select existing execution infrastructure behind one stable boundary."""
    if browser_mode == "live":
        if live_driver == "cdp" or cdp_endpoint:
            from mcp_vision.live_browser import LiveBrowserRuntime
            return LiveBrowserRuntime(endpoint=cdp_endpoint, **options)
        from mcp_vision.native_browser import NativeBrowserRuntime
        return NativeBrowserRuntime(**options)
    if browser_mode == "isolated":
        if cdp_endpoint:
            raise ValueError("cdp_endpoint requires browser_mode=live")
        from mcp_vision.browser import BrowserRuntime
        return BrowserRuntime(**options)
    raise ValueError("browser_mode must be live or isolated")


async def bind_context_backend(context, *, mode, live_driver='native', cdp_endpoint=None,
                               indicator=None, factory=create_execution_backend):
    if mode == 'ask':
        return None
    if context.source == 'macos' and not context.url:
        from mcp_vision.native_context import NativeContextBackend
        if mode == 'act':
            raise ValueError('Native Act is not available in this contextual backend. Switch to Guide.')
        return NativeContextBackend(context, indicator)
    if not context.url:
        raise ValueError('Invoke on a browser page to select an execution target.')
    backend = factory(browser_mode='live', live_driver=live_driver, cdp_endpoint=cdp_endpoint,
                      allow_writes=mode == 'act')
    try:
        listing = await backend.tabs()
        matches = [tab for tab in listing.get('tabs', []) if tab['url'] == context.url]
        if len(matches) != 1:
            raise ValueError('The contextual tab is missing or ambiguous. Keep one matching tab open and invoke again.')
        receipt = await backend.use_tab(matches[0]['tab_id'], context.url)
        if receipt.status != 'verified':
            raise ValueError(receipt.message)
        return backend
    except Exception:
        await backend.close()
        raise

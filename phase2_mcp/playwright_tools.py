r"""
Playwright helpers for browser windows, exposed through the MCP server.
Not used by simple_agent.py, which drives the browser through the screen.

Connects to existing Chrome via CDP instead of launching a new instance.
Run Chrome with: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222
"""

import asyncio
import os
from typing import Any, Optional

from loguru import logger

try:
    from playwright.async_api import async_playwright, Browser, Page, Playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    logger.warning("Playwright not installed. Run: pip install playwright && playwright install")

# Default CDP endpoint for existing Chrome
DEFAULT_CDP_ENDPOINT = "http://localhost:9222"


class PlaywrightManager:
    def __init__(self, cdp_endpoint: str | None = None):
        from config import cfg
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self._lock = asyncio.Lock() if HAS_PLAYWRIGHT else None
        self.cdp_endpoint = cdp_endpoint or cfg.playwright_cdp_endpoint
        self._owns_browser = False  # True if we launched it, False if attached

    async def start(self) -> bool:
        if not HAS_PLAYWRIGHT:
            return False

        async with self._lock:
            if self.browser is not None:
                return True

            try:
                self.playwright = await async_playwright().start()

                # Try to connect to existing Chrome via CDP
                try:
                    logger.info(f"Connecting to Chrome at {self.cdp_endpoint}...")
                    self.browser = await self.playwright.chromium.connect_over_cdp(self.cdp_endpoint)
                    logger.info("Successfully attached to existing Chrome instance")
                except Exception as e:
                    logger.info("Chrome remote debugging is not active on port 9222. Falling back to visual/accessibility GUI control (pyautogui + OmniParser). This is normal and expected.")
                    # Do NOT launch a new un-profiled Chrome session.
                    # This ensures the agent uses visual/AX perception on the user's real browser instead.
                    return False

                # Get or create a page
                contexts = self.browser.contexts
                if contexts:
                    pages = contexts[0].pages
                    self.page = pages[0] if pages else await contexts[0].new_page()
                else:
                    context = await self.browser.new_context()
                    self.page = await context.new_page()

                self.page.set_default_timeout(10000)
                return True
            except Exception as e:
                logger.error(f"Failed to start Playwright: {e}")
                await self.stop()
                return False

    async def stop(self):
        async with self._lock:
            if self.page:
                try:
                    await self.page.close()
                except Exception:
                    pass
                self.page = None

            if self.browser:
                try:
                    if self._owns_browser:
                        await self.browser.close()
                    else:
                        # Just disconnect, don't close the user's browser
                        pass
                except Exception:
                    pass
                self.browser = None

            if self.playwright:
                try:
                    await self.playwright.stop()
                except Exception:
                    pass
                self.playwright = None

    async def is_running(self) -> bool:
        return self.browser is not None and self.page is not None

    async def navigate(self, url: str, timeout: int = 15000) -> bool:
        if not await self.is_running():
            return False
        try:
            if "://" not in url:
                url = "https://" + url
            await self.page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            return True
        except Exception as e:
            logger.debug(f"Playwright navigate failed: {e}")
            return False

    async def click_element_by_role(self, role: str, name: Optional[str] = None, timeout: int = 5000) -> bool:
        if not await self.is_running():
            return False
        try:
            locator = self.page.get_by_role(role, name=name) if name else self.page.get_by_role(role)
            await locator.click(timeout=timeout)
            return True
        except Exception as e:
            logger.debug(f"Playwright click by role failed: {e}")
            return False

    async def click_element_by_text(self, text: str, timeout: int = 5000) -> bool:
        if not await self.is_running():
            return False
        try:
            await self.page.get_by_text(text, exact=False).click(timeout=timeout)
            return True
        except Exception as e:
            logger.debug(f"Playwright click by text failed: {e}")
            return False

    async def type_text_into_input(self, selector: str, text: str, timeout: int = 5000, delay: int = 50) -> bool:
        if not await self.is_running():
            return False
        try:
            await self.page.fill(selector, "", timeout=timeout)
            await self.page.type(selector, text, delay=delay, timeout=timeout)
            return True
        except Exception as e:
            logger.debug(f"Playwright type failed: {e}")
            return False

    async def type_text(self, text: str, delay: int = 20) -> bool:
        if not await self.is_running():
            return False
        try:
            await self.page.keyboard.type(text, delay=delay)
            return True
        except Exception as e:
            logger.debug(f"Playwright typing failed: {e}")
            return False

    async def press_key(self, key: str, timeout: int = 5000) -> bool:
        if not await self.is_running():
            return False
        try:
            await self.page.keyboard.press(key, timeout=timeout)
            return True
        except Exception as e:
            logger.debug(f"Playwright key press failed: {e}")
            return False

    async def scroll(self, direction: str, clicks: int) -> bool:
        if not await self.is_running():
            return False
        try:
            amount = abs(clicks) * 100
            await self.page.mouse.wheel(0, amount if direction == "down" else -amount)
            return True
        except Exception as e:
            logger.debug(f"Playwright scroll failed: {e}")
            return False

    async def get_page_text(self, timeout: int = 5000) -> Optional[str]:
        if not await self.is_running():
            return None
        try:
            text = await self.page.text_content("body", timeout=timeout)
            return text.strip() if text else None
        except Exception as e:
            logger.debug(f"Playwright get text failed: {e}")
            return None


_playwright_manager: Optional[PlaywrightManager] = None


def _get_playwright_manager() -> PlaywrightManager:
    global _playwright_manager
    if _playwright_manager is None:
        _playwright_manager = PlaywrightManager()
    return _playwright_manager


async def _ensure_playwright_started() -> bool:
    manager = _get_playwright_manager()
    if not await manager.is_running():
        return await manager.start()
    return True


def _run_async(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            raise RuntimeError("Cannot run async code from within running event loop")
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def navigate(url: str) -> str:
    if not HAS_PLAYWRIGHT:
        return "ERROR: Playwright not available"

    async def _nav():
        if not await _ensure_playwright_started():
            return False
        return await _get_playwright_manager().navigate(url)

    if _run_async(_nav()):
        return f"Navigated to {url}"
    return f"ERROR: Failed to navigate to {url} (is Chrome running with --remote-debugging-port=9222?)"


def click_element_by_role(role: str, name: Optional[str] = None) -> str:
    if not HAS_PLAYWRIGHT:
        return "ERROR: Playwright not available"

    async def _click():
        if not await _ensure_playwright_started():
            return False
        return await _get_playwright_manager().click_element_by_role(role, name)

    if _run_async(_click()):
        suffix = f" '{name}'" if name else ""
        return f"Clicked {role}{suffix} via Playwright"
    return f"ERROR: Failed to click {role} via Playwright"


def click_element_by_text(text: str) -> str:
    if not HAS_PLAYWRIGHT:
        return "ERROR: Playwright not available"

    async def _click():
        if not await _ensure_playwright_started():
            return False
        return await _get_playwright_manager().click_element_by_text(text)

    if _run_async(_click()):
        return f"Clicked '{text}' via Playwright"
    return f"ERROR: Failed to click text via Playwright"


def type_text_into_input(selector: str, text: str) -> str:
    if not HAS_PLAYWRIGHT:
        return "ERROR: Playwright not available"

    async def _type():
        if not await _ensure_playwright_started():
            return False
        return await _get_playwright_manager().type_text_into_input(selector, text)

    if _run_async(_type()):
        return f"Typed into {selector} via Playwright"
    return f"ERROR: Failed to type into {selector} via Playwright"


def type_text(text: str) -> str:
    if not HAS_PLAYWRIGHT:
        return "ERROR: Playwright not available"

    async def _type():
        if not await _ensure_playwright_started():
            return False
        return await _get_playwright_manager().type_text(text)

    if _run_async(_type()):
        return f"Typed {len(text)} characters via Playwright"
    return "ERROR: Failed to type via Playwright"


def press_key(key: str) -> str:
    if not HAS_PLAYWRIGHT:
        return "ERROR: Playwright not available"

    async def _press():
        if not await _ensure_playwright_started():
            return False
        return await _get_playwright_manager().press_key(key)

    if _run_async(_press()):
        return f"Pressed '{key}' via Playwright"
    return f"ERROR: Failed to press '{key}' via Playwright"


def scroll(direction: str = "down", clicks: int = 3) -> str:
    if direction not in {"up", "down"}:
        return "ERROR: direction must be 'up' or 'down'"
    if clicks < 1:
        return "ERROR: clicks must be at least 1"
    if not HAS_PLAYWRIGHT:
        return "ERROR: Playwright not available"

    async def _scroll():
        if not await _ensure_playwright_started():
            return False
        return await _get_playwright_manager().scroll(direction, clicks)

    if _run_async(_scroll()):
        return f"Scrolled {direction} {clicks} ticks via Playwright"
    return f"ERROR: Failed to scroll {direction} via Playwright"


def get_page_text() -> str:
    if not HAS_PLAYWRIGHT:
        return "ERROR: Playwright not available"

    async def _get():
        if not await _ensure_playwright_started():
            return None
        return await _get_playwright_manager().get_page_text()

    text = _run_async(_get())
    if text:
        preview = text[:4000] + ("..." if len(text) > 4000 else "")
        return f"Page text: {preview}"
    return "ERROR: Failed to get page text via Playwright"


def is_browser_available() -> bool:
    if not HAS_PLAYWRIGHT:
        return False
    try:
        return _run_async(_get_playwright_manager().is_running())
    except Exception:
        return False


def cleanup_playwright():
    global _playwright_manager
    if _playwright_manager is not None:
        _run_async(_playwright_manager.stop())
        _playwright_manager = None

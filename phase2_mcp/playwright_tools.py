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

# A freshly-navigated SPA (Chrome DeepMind/React/Vue sites, etc.) often
# answers the very first read with just its shell — nav bar, "Skip to
# content", a footer — before the body actually hydrates. That thin read
# used to be handed straight to the model, which had no way to tell "page is
# genuinely short" from "page hasn't loaded yet" and would give up into the
# (slow, imprecise) guide_user vision fallback. min_chars is a blunt but
# effective proxy: real article/page content clears it, nav chrome doesn't.
THIN_TEXT_CHARS = 350


def _looks_thin(text, min_chars=THIN_TEXT_CHARS):
    return not text or len(text.strip()) < min_chars


async def _read_with_retry(read_once, wait_s=0.9, min_chars=THIN_TEXT_CHARS):
    """Call read_once() (an async callable returning str|None). If the first
    read looks like only nav/boilerplate loaded, wait once for hydration and
    retry, keeping whichever read is longer. Pure asyncio — no Playwright
    handle required, so it's testable without Chrome or the playwright
    package installed."""
    first = await read_once()
    if not _looks_thin(first, min_chars):
        return first
    await asyncio.sleep(wait_s)
    second = await read_once()
    candidates = [c for c in (first, second) if c]
    return max(candidates, key=len) if candidates else None


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
            await locator.first.click(timeout=timeout)
            return True
        except Exception as e:
            logger.debug(f"Playwright click by role failed: {e}")
            return False

    async def click_element_by_text(self, text: str, timeout: int = 5000) -> bool:
        if not await self.is_running():
            return False
        try:
            # .first: a page often has several matches; strict mode would raise
            await self.page.get_by_text(text, exact=False).first.click(timeout=timeout)
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

        async def _once():
            try:
                # inner_text = rendered visible text only; text_content would
                # drag in <style>/<script> bodies and hand the model CSS
                # instead of page content
                text = await self.page.inner_text("body", timeout=timeout)
                return text.strip() if text else None
            except Exception as e:
                logger.debug(f"Playwright get text failed: {e}")
                return None

        return await _read_with_retry(_once)


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


def demo():
    assert _looks_thin("")
    assert _looks_thin("Skip to main content\nGoogle DeepMind\nModels\nTry Gemini")
    assert not _looks_thin("x" * (THIN_TEXT_CHARS + 1))

    # thin first read -> waits and retries -> keeps the richer second read
    calls = []
    async def flaky_then_hydrated():
        calls.append(1)
        return "Skip to content · Nav · Menu" if len(calls) == 1 else "article body " * 50

    result = asyncio.run(_read_with_retry(flaky_then_hydrated, wait_s=0.01))
    assert result == "article body " * 50 and len(calls) == 2

    # already-rich first read -> no retry, no wasted wait
    calls2 = []
    async def rich_immediately():
        calls2.append(1)
        return "y" * 500
    assert asyncio.run(_read_with_retry(rich_immediately, wait_s=0.01)) == "y" * 500
    assert len(calls2) == 1

    # both reads empty -> None, never crashes
    async def always_none():
        return None
    assert asyncio.run(_read_with_retry(always_none, wait_s=0.01)) is None

    # both reads thin -> keeps the longer of the two rather than giving up
    calls3 = []
    async def thin_both_times():
        calls3.append(1)
        return "short a" if len(calls3) == 1 else "short bb"
    assert asyncio.run(_read_with_retry(thin_both_times, wait_s=0.01)) == "short bb"

    print("ok")


if __name__ == "__main__":
    demo()

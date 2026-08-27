r"""
Browser control for the agent: attach over CDP, read, and act.

Attaches to a browser already running with --remote-debugging-port=9222 (so it
uses your real, logged-in session); if nothing is listening it launches a
Chromium-family browser with a throwaway profile. See find_chrome() for the
per-platform lookup, or set SCREEN_AGENT_CHROME_PATH.

What the model sees and does is decided by the browser, not guessed:
  - page_snapshot.py turns the page into role + accessible name, drops anything
    an overlay is covering, and records a verified click point per element;
  - clicks are real mouse clicks at those verified points;
  - micro_vision.py is the last resort, pointing at a small cropped thumbnail.
"""

import asyncio
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Optional

from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from phase1_vision.coords import VIEWPORT_METRICS_JS, from_browser
from phase2_mcp import micro_vision
from phase2_mcp.page_snapshot import (
    REACHABLE_JS,
    SCROLL_INTO_CENTER_JS,
    SNAPSHOT_JS,
    crop_box,
    format_elements,
)
from runtime import PAGE_FINGERPRINT_JS, did_state_change

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


# Read the page as *content*, not chrome: strip script/style/nav/header/footer
# so the model gets the article/product text rather than the same global menu
# on every read.
_READ_JS = r"""() => {
  const c = document.body.cloneNode(true);
  c.querySelectorAll('script,style,noscript,svg,nav,header,footer,[role=navigation],[aria-hidden=true]')
    .forEach(n => n.remove());
  return (c.innerText || '').replace(/\n{3,}/g, '\n\n').trim();
}"""

# Clicks whose accessible name looks like an irreversible/outbound control are
# refused in code — the agent must never auto-buy or auto-submit. This is a hard
# trust boundary, not a prompt the model can talk its way past.
_RISKY_CLICK = ("buy", "purchase", "checkout", "place order", "add to cart", "add to bag",
                "pay", "order now", "submit", "send", "post", "publish", "delete",
                "confirm", "subscribe", "sign up", "place bid", "continue to payment")

# A control is labelled tersely ("Buy", "Add to Bag") or leads with its verb
# ("Buy Pro Plan, annual billing"). Marketing prose is long and only *mentions*
# the word further in ("...get a gift card when you buy X"), which is a
# sentence, not a button.
_LABEL_WORDS = 6
_RISKY_RE = re.compile(r"\b(?:" + "|".join(re.escape(k) for k in _RISKY_CLICK) + r")\b")


def _looks_risky(label):
    """Conservative by design: when in doubt, refuse and let a human decide.

    Whole-word matches only. Fires on any short label, and on a long one only
    when it *starts* with the risky verb — so a genuine "Buy <long product
    name>" is still blocked, while a paragraph that merely contains "buy" is
    not. Erring toward blocking is deliberate: a false positive costs one
    handoff to the user, a false negative could spend their money.
    """
    low = " ".join((label or "").lower().split())
    if not low:
        return False
    if len(low.split()) <= _LABEL_WORDS:
        return bool(_RISKY_RE.search(low))
    return any(low.startswith(k) for k in _RISKY_CLICK)


# Where a Chromium-family browser lives, per platform. Checked in order; the
# first one that exists wins. Set cfg.chrome_path to override.
_CHROME_CANDIDATES = {
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    ],
    "win32": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ],
    "linux": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"],
}


def split_endpoint(endpoint):
    """('http://localhost:9222') -> ('localhost', 9222)."""
    hostport = endpoint.split("://", 1)[-1].split("/", 1)[0]
    host, _, port = hostport.partition(":")
    return host or "localhost", int(port or 80)


async def cdp_listening(endpoint, timeout=1.0):
    """Is anything accepting connections on the debug port?

    A cheap TCP probe. connect_over_cdp() waits 30s by default before giving
    up, which turns "no browser running" into a very long stall, so we check
    first and only call it when there is something to talk to.
    """
    host, port = split_endpoint(endpoint)
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


def find_chrome(configured=None, platform=None, exists=os.path.exists, which=shutil.which):
    """First available Chromium-family browser, or None.

    Bare names are resolved on PATH, absolute paths are checked directly, so
    the same list works for a Linux package install and a macOS .app bundle.
    """
    if configured:
        return configured
    key = platform or sys.platform
    key = "linux" if key.startswith("linux") else key
    for path in _CHROME_CANDIDATES.get(key, []):
        # "looks like a path" can't use os.path.isabs here: a Windows path is
        # not absolute by POSIX rules, and this runs cross-platform in tests.
        if "/" in path or "\\" in path:
            if exists(path):
                return path
        else:
            found = which(path)
            if found:
                return found
    return None


class PlaywrightManager:
    def __init__(self, cdp_endpoint: str | None = None):
        from config import cfg
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self._lock = asyncio.Lock() if HAS_PLAYWRIGHT else None
        self.cdp_endpoint = cdp_endpoint or cfg.playwright_cdp_endpoint
        self._owns_browser = False  # True if we launched it, False if attached
        self._proc = None           # the browser process, when we launched it
        self.index_labels = {}      # index -> accessible name, latest snapshot
        self.elements = {}          # index -> full element record, latest snapshot
        self.viewport = None        # Viewport from the last snapshot
        self.snapshot_source = "dom"

    async def start(self) -> bool:
        if not HAS_PLAYWRIGHT:
            return False

        from config import cfg

        async with self._lock:
            if self.browser is not None:
                return True

            try:
                self.playwright = await async_playwright().start()

                if await cdp_listening(self.cdp_endpoint):
                    logger.info(f"Attaching to the browser on {self.cdp_endpoint}")
                    self.browser = await self.playwright.chromium.connect_over_cdp(
                        self.cdp_endpoint, timeout=10000)
                else:
                    browser_path = find_chrome(getattr(cfg, "chrome_path", None))
                    if not browser_path:
                        logger.error(
                            "No Chromium-family browser found. Start one with "
                            "--remote-debugging-port=9222, or set SCREEN_AGENT_CHROME_PATH."
                        )
                        return False

                    import subprocess
                    import tempfile
                    _, port = split_endpoint(self.cdp_endpoint)
                    profile = os.path.join(tempfile.gettempdir(), "agent_browser_profile")
                    logger.info(f"Nothing on {self.cdp_endpoint}; launching {browser_path}")
                    self._proc = subprocess.Popen(
                        [browser_path, f"--remote-debugging-port={port}",
                         f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )

                    ready = False
                    for _ in range(20):  # ~10s, probing a port rather than stalling on connect
                        await asyncio.sleep(0.5)
                        if await cdp_listening(self.cdp_endpoint):
                            ready = True
                            break
                    if not ready:
                        # On macOS, launching the binary while that browser is
                        # already running just hands off to the running copy and
                        # exits, so the debug port never opens. Say so plainly —
                        # this is the one case the user has to resolve.
                        delegated = self._proc.poll() is not None
                        logger.error(
                            f"Launched {browser_path} but nothing is listening on {self.cdp_endpoint}."
                            + (" It handed off to an already-running browser; quit that browser and"
                               " retry, or start it yourself with --remote-debugging-port."
                               if delegated else "")
                        )
                        return False

                    self.browser = await self.playwright.chromium.connect_over_cdp(
                        self.cdp_endpoint, timeout=10000)
                    self._owns_browser = True
                    logger.info("Attached to the launched browser")

                self.page = await asyncio.wait_for(self._acquire_page(), timeout=20)
                if self.page is None:
                    logger.error("Attached to the browser but could not obtain a page")
                    return False
                self.page.set_default_timeout(10000)
                return True
            except Exception as e:
                logger.error(f"Failed to start Playwright: {e}")
                # _teardown, not stop(): we already hold the lock and it is not
                # reentrant, so stop() here would deadlock instead of reporting.
                await self._teardown()
                return False

    async def _acquire_page(self):
        """Get a usable page from an attached browser.

        A CDP-attached browser only ever has its default context, and it can
        legitimately have zero tabs (every window closed). new_context() is not
        supported on that connection and will hang, so reuse the existing
        context and open a tab in it; only fall back to new_context() when the
        browser genuinely reports none.
        """
        contexts = self.browser.contexts
        if contexts:
            context = contexts[0]
        else:
            try:
                context = await self.browser.new_context()
            except Exception as e:
                logger.debug(f"new_context unsupported on this connection: {e}")
                return None
        live = [p for p in context.pages if not p.is_closed()]
        return live[0] if live else await context.new_page()

    async def stop(self):
        async with self._lock:
            await self._teardown()

    async def _teardown(self):
        """Release everything. Caller must already hold the lock (or be sure no
        one else is using the manager)."""
        # Never close a page or window the user opened — we only disconnect
        # from a browser that was already theirs.
        if self._owns_browser and self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
        self.page = None
        self.browser = None

        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass
            self.playwright = None

        # close() over CDP only drops the connection; the process we launched
        # would otherwise survive holding the debug port with no tabs, and the
        # next attach would find an unusable browser.
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
            self._owns_browser = False

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

    async def snapshot(self, max_elements: int = 60):
        """Semantic, occlusion-pruned snapshot of what can actually be clicked."""
        if not await self.is_running():
            return None
        # A client-rendered page paints an empty shell first; interactive
        # elements only exist after hydration/layout. Retry before concluding
        # the page is genuinely empty.
        snap = None
        for attempt in range(3):
            try:
                snap = await self.page.evaluate(SNAPSHOT_JS, max_elements)
            except Exception as e:
                # A broken snapshot script looks exactly like an empty page, so
                # this is worth a real warning rather than a debug line.
                logger.warning(f"snapshot script failed: {e}")
                snap = None
            if snap and snap.get("elements"):
                break
            if attempt < 2:
                await asyncio.sleep(1.0)
        els = (snap or {}).get("elements") or []
        self.snapshot_source = "dom"
        try:
            metrics = await self.page.evaluate(VIEWPORT_METRICS_JS)
            size = self.page.viewport_size or {}
            shot = (size.get("width"), size.get("height")) if size.get("width") else None
            self.viewport = from_browser(metrics, shot)
            if snap is not None:
                snap["viewport"] = metrics
        except Exception as e:
            logger.debug(f"viewport metrics failed: {e}")

        if not els:
            vis = await self._visual_fallback()
            if vis:
                els = vis
                snap = {**(snap or {}), "elements": els, "source": "visual"}
                self.snapshot_source = "visual"

        self.index_labels = {e["index"]: e.get("name", "") for e in els}
        self.elements = {e["index"]: e for e in els}
        return snap

    async def _visual_fallback(self):
        """Canvas / empty-DOM path: SoM boxes from the screenshot, in CSS px."""
        try:
            import io
            from PIL import Image
            from phase1_vision.grounding import from_visual
            png = await self.page.screenshot()
            img = Image.open(io.BytesIO(png))
            vp = self.viewport or from_browser(
                {"innerWidth": img.width, "innerHeight": img.height, "devicePixelRatio": 1})
            vp.screenshot_w, vp.screenshot_h = float(img.width), float(img.height)
            boxes = from_visual(img, vp)
        except Exception as e:
            logger.debug(f"visual SoM fallback failed: {e}")
            return []
        out = []
        for i, b in enumerate(boxes):
            out.append({
                "index": i, "role": b.get("role") or "visual",
                "name": b.get("label") or f"region {i}",
                "cx": b["cx"], "cy": b["cy"],
                "x": b["x"], "y": b["y"], "w": b["w"], "h": b["h"],
            })
        return out

    async def _reachable(self, sel):
        """Where this element can actually be hit right now, or None."""
        try:
            return await self.page.evaluate(REACHABLE_JS, sel)
        except Exception as e:
            logger.debug(f"reachability probe failed: {e}")
            return None

    async def fingerprint(self):
        if not await self.is_running():
            return None
        try:
            return await self.page.evaluate(PAGE_FINGERPRINT_JS)
        except Exception as e:
            logger.debug(f"fingerprint failed: {e}")
            return None

    async def _click_and_verify(self, x, y, before, how):
        try:
            await self.page.mouse.click(x, y)
        except Exception as e:
            logger.debug(f"{how} click failed: {e}")
            return False, how
        await asyncio.sleep(0.15)
        after = await self.fingerprint()
        if did_state_change(before, after):
            return True, how
        return False, how + "-noop"

    async def click_index(self, index: int, timeout: int = 8000):
        """Click an element from the last snapshot. Returns (ok, how).

        After every click, fingerprint the page. A mouse event that hits a
        modal scrim and changes nothing is a failed action, not a success —
        we then try scroll-into-center, Escape (dismiss overlay), and
        micro-vision, in that order.
        """
        if not await self.is_running():
            return False, "no browser"
        sel = f'[data-agent-index="{index}"]'
        before = await self.fingerprint()

        hit = await self._reachable(sel)
        if hit:
            ok, how = await self._click_and_verify(hit["cx"], hit["cy"], before, "click")
            if ok:
                return True, how

        try:
            await self.page.evaluate(SCROLL_INTO_CENTER_JS, sel)
            await asyncio.sleep(0.35)
        except Exception as e:
            logger.debug(f"scroll into centre failed: {e}")

        hit = await self._reachable(sel)
        if hit:
            ok, how = await self._click_and_verify(hit["cx"], hit["cy"], before, "scroll+click")
            if ok:
                return True, how

        try:
            await self.page.keyboard.press("Escape")
            await asyncio.sleep(0.2)
        except Exception as e:
            logger.debug(f"dismiss overlay failed: {e}")

        hit = await self._reachable(sel)
        if hit:
            ok, how = await self._click_and_verify(hit["cx"], hit["cy"], before, "dismiss+click")
            if ok:
                return True, how

        if await self._micro_vision_click(sel, index):
            after = await self.fingerprint()
            if did_state_change(before, after):
                return True, "micro-vision"
            return False, "micro-vision-noop"
        return False, "occluded"

    async def _micro_vision_click(self, sel, index):
        """Crop a thumbnail around the element and let a small VLM point at it."""
        from config import cfg

        if not getattr(cfg, "micro_vision", True):
            return False
        box = await self._reachable(sel)
        if not box:  # fall back to the geometry recorded at snapshot time
            box = self.elements.get(index)
        if not box:
            return False

        try:
            size = self.page.viewport_size or {}
            viewport = (size.get("width"), size.get("height")) if size.get("width") else None
            clip = crop_box(box, viewport=viewport)
            png = await self.page.screenshot(clip=clip)
        except Exception as e:
            logger.debug(f"micro-vision: crop failed: {e}")
            return False

        label = self.index_labels.get(index) or f"element {index}"
        point, img_size = micro_vision.locate(
            png, label, model=cfg.model, keep_alive=cfg.ollama_keep_alive)
        if not point:
            return False

        mapped = micro_vision.to_viewport(point[0], point[1], img_size, clip)
        if not mapped:
            return False
        try:
            await self.page.mouse.click(mapped[0], mapped[1])
            logger.info(f"micro-vision clicked '{label}' at {mapped}")
            return True
        except Exception as e:
            logger.debug(f"micro-vision click failed: {e}")
            return False

    async def type_into_index(self, index: int, text: str, timeout: int = 8000) -> bool:
        if not await self.is_running():
            return False
        try:
            sel = f'[data-agent-index="{index}"]'
            await self.page.click(sel, timeout=timeout)
            await self.page.fill(sel, text, timeout=timeout)
            return True
        except Exception as e:
            logger.debug(f"type_into_index failed: {e}")
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
                # strip nav/header/footer/script/style so the model gets the
                # actual page content, not the same global menu every read
                text = await self.page.evaluate(_READ_JS)
                if not text:  # fallback if evaluate is blocked
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


def snapshot() -> str:
    if not HAS_PLAYWRIGHT:
        return "ERROR: Playwright not available"

    async def _snap():
        if not await _ensure_playwright_started():
            return None
        return await _get_playwright_manager().snapshot()

    return format_elements(_run_async(_snap()))


def click_index(index) -> str:
    if not HAS_PLAYWRIGHT:
        return "ERROR: Playwright not available"
    try:
        index = int(index)
    except (TypeError, ValueError):
        return f"ERROR: index must be a number, got {index!r}"

    mgr = _get_playwright_manager()
    label = mgr.index_labels.get(index, "")
    if _looks_risky(label):
        return (f"BLOCKED: element [{index}] '{label}' looks like a purchase/submit/"
                f"irreversible control. I won't click it automatically — use guide_user "
                f"to have the user click it.")

    async def _click():
        if not await _ensure_playwright_started():
            return False, "no browser"
        return await mgr.click_index(index)

    ok, how = _run_async(_click())
    if ok:
        return f"Clicked [{index}] '{label}'" + ("" if how == "click" else f" (via {how})")
    if how == "occluded":
        return (f"ERROR: [{index}] '{label}' is covered by an overlay and cannot be clicked. "
                f"Close any cookie/consent banner or modal first, or use guide_user.")
    if how.endswith("-noop"):
        return (f"ERROR: [{index}] '{label}' click landed but the page did not change ({how}). "
                f"A modal may be intercepting it — close it, then web_snapshot and retry.")
    return (f"ERROR: could not click [{index}] '{label}' ({how}). The page may have changed — "
            f"call web_snapshot again for fresh indices, then retry.")


def type_into_index(index, text) -> str:
    if not HAS_PLAYWRIGHT:
        return "ERROR: Playwright not available"
    try:
        index = int(index)
    except (TypeError, ValueError):
        return f"ERROR: index must be a number, got {index!r}"

    async def _type():
        if not await _ensure_playwright_started():
            return False
        return await _get_playwright_manager().type_into_index(index, text)

    if _run_async(_type()):
        return f"Typed into [{index}]"
    return f"ERROR: could not type into [{index}]. Re-run web_snapshot and retry."


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
    return f"ERROR: Failed to click text '{text}'. Text not found on page. Read the page to find exact text."


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

    # risky-click guard. Must not miss a real control...
    assert _looks_risky("Buy") and _looks_risky("Place Order") and _looks_risky("Checkout")
    assert _looks_risky("Continue to payment") and _looks_risky("Delete account")
    assert _looks_risky("Add to Bag") and _looks_risky("Submit") and _looks_risky("Publish")
    # ...including one with a long product name after the verb
    assert _looks_risky("Buy Pro Plan annual billing 20 seats included")
    # ...and must ignore ordinary navigation
    assert not _looks_risky("Learn more") and not _looks_risky("Documentation")
    assert not _looks_risky("") and not _looks_risky(None)
    # ...and prose that merely mentions a verb is a sentence, not a button
    assert not _looks_risky(
        "For a limited time, get a $150 gift card when you buy a laptop with education savings")
    # whole words only: "buyer" is not "buy", "deleted" is not "delete"
    assert not _looks_risky("Buyer guide") and not _looks_risky("Recently deleted items")

    # endpoint parsing feeds both the port probe and the launch flag
    assert split_endpoint("http://localhost:9222") == ("localhost", 9222)
    assert split_endpoint("http://127.0.0.1:9333/") == ("127.0.0.1", 9333)

    # probing a port nothing is bound to fails fast instead of stalling
    t0 = time.monotonic()
    assert asyncio.run(cdp_listening("http://127.0.0.1:1", timeout=0.5)) is False
    assert time.monotonic() - t0 < 5

    # browser lookup is per-platform and honours an explicit override
    assert find_chrome("/custom/browser") == "/custom/browser"
    assert find_chrome(None, platform="darwin", exists=lambda p: True).endswith("Google Chrome")
    assert find_chrome(None, platform="linux", exists=lambda p: False,
                       which=lambda n: f"/usr/bin/{n}") == "/usr/bin/google-chrome"
    assert find_chrome(None, platform="win32", exists=lambda p: "chrome.exe" in p).endswith("chrome.exe")
    # nothing installed -> None, so the caller can report it instead of crashing
    assert find_chrome(None, platform="linux", exists=lambda p: False, which=lambda n: None) is None
    assert find_chrome(None, platform="plan9", exists=lambda p: True) is None

    from runtime import did_state_change, coerce_args
    assert coerce_args({"index": "4"})["index"] == 4
    fp = {"url": "x", "title": "t", "n": 1, "scroll": 0, "text": "aaaa"}
    assert not did_state_change(fp, dict(fp))
    assert did_state_change(fp, {**fp, "n": 9})

    print("ok")


if __name__ == "__main__":
    demo()

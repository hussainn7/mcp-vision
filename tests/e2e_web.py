"""Headless Playwright e2e: snapshot, click, forms, modal dismiss, risky-buy.

    python tests/e2e_web.py

Uses real Chromium against local HTML fixtures. Skips cleanly if Playwright
or a browser binary isn't installed.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURES = Path(__file__).parent / "fixtures"


def _playwright_ok():
    try:
        from playwright.async_api import async_playwright  # noqa: F401
        return True
    except ImportError:
        return False


async def _page(pw, html_path: Path):
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page(viewport={"width": 800, "height": 600})
    await page.goto(html_path.as_uri())
    return browser, page


async def _mgr(page, browser, pw):
    from phase2_mcp.playwright_tools import PlaywrightManager
    mgr = PlaywrightManager()
    mgr.playwright = pw
    mgr.browser = browser
    mgr.page = page
    mgr._owns_browser = False
    return mgr


async def test_issue_form(pw):
    """Fill a GitHub-like issue form and click Create issue."""
    browser, page = await _page(pw, FIXTURES / "issue_form.html")
    try:
        mgr = await _mgr(page, browser, pw)
        snap = await mgr.snapshot()
        els = {e["name"]: e for e in snap["elements"]}
        assert "Title" in els and "Create issue" in els, snap
        title = els["Title"]
        ok = await mgr.type_into_index(title["index"], "login broken")
        assert ok
        body = els.get("Details") or els.get("Body")
        if body:
            await mgr.type_into_index(body["index"], "repro on safari")
        create = els["Create issue"]
        ok, how = await mgr.click_index(create["index"])
        assert ok, how
        status = await page.inner_text("#status")
        assert "opened: login broken" in status, status
    finally:
        await browser.close()


async def test_modal_occlusion(pw):
    """Cookie banner covers Save; snapshot omits it; Accept then Save works."""
    from config import cfg
    from phase2_mcp.page_snapshot import format_elements
    saved_mv = cfg.micro_vision
    cfg.micro_vision = False
    browser, page = await _page(pw, FIXTURES / "modal.html")
    try:
        mgr = await _mgr(page, browser, pw)
        snap = await mgr.snapshot()
        names = [e["name"] for e in snap["elements"]]
        assert "Accept cookies" in names, names
        assert "Save draft" not in names, names  # occluded, pruned
        assert (snap.get("pruned") or {}).get("occluded", 0) >= 1
        menu = format_elements(snap)
        assert "Accept cookies" in menu and "Save draft" not in menu

        accept = next(e for e in snap["elements"] if e["name"] == "Accept cookies")
        ok, how = await mgr.click_index(accept["index"])
        assert ok, how
        assert "cleared" in await page.inner_text("#status")

        snap2 = await mgr.snapshot()
        names2 = [e["name"] for e in snap2["elements"]]
        assert "Save draft" in names2, names2
        save = next(e for e in snap2["elements"] if e["name"] == "Save draft")
        ok, how = await mgr.click_index(save["index"])
        assert ok, how
        assert await page.inner_text("#status") == "saved"
    finally:
        cfg.micro_vision = saved_mv
        await browser.close()


async def test_shop_risky_and_cart(pw):
    """Add to cart is blocked in code; Details is a safe click."""
    from phase2_mcp.playwright_tools import _looks_risky
    browser, page = await _page(pw, FIXTURES / "shop.html")
    try:
        mgr = await _mgr(page, browser, pw)
        snap = await mgr.snapshot()
        names = {e["name"]: e for e in snap["elements"]}
        assert "Add to cart" in names and "Buy" in names
        assert _looks_risky("Add to cart") and _looks_risky("Buy")
        assert not _looks_risky("Details")

        # the wrapper refuses before the mouse moves
        mgr.index_labels = {e["index"]: e["name"] for e in snap["elements"]}
        from phase2_mcp import playwright_tools as pt
        saved, pt._playwright_manager = pt._playwright_manager, mgr

        async def fake_ensure():
            return True
        saved_ensure, pt._ensure_playwright_started = pt._ensure_playwright_started, fake_ensure
        try:
            # click_index sync wrapper uses _run_async; call the risky check directly
            buy = names["Buy"]
            assert _looks_risky(mgr.index_labels[buy["index"]])
            details = names["Details"]
            ok, how = await mgr.click_index(details["index"])
            assert ok, how
            assert page.url.endswith("#details")
        finally:
            pt._playwright_manager = saved
            pt._ensure_playwright_started = saved_ensure
    finally:
        await browser.close()


async def test_noop_click_is_not_success(pw):
    """A button with no handler must not count as a successful state change."""
    from config import cfg
    saved = cfg.micro_vision
    cfg.micro_vision = False
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.set_content("<button>Dead</button><p id='x'>same</p>")
    try:
        mgr = await _mgr(page, browser, pw)
        snap = await mgr.snapshot()
        assert snap["elements"], snap
        dead = snap["elements"][0]
        ok, how = await mgr.click_index(dead["index"])
        assert not ok, (ok, how)
        assert "noop" in how or how == "occluded"
    finally:
        cfg.micro_vision = saved
        await browser.close()


async def test_viewport_metrics(pw):
    from phase1_vision.coords import VIEWPORT_METRICS_JS, from_browser, css_to_screenshot
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page(viewport={"width": 640, "height": 360})
    await page.set_content("<p>hi</p>")
    try:
        metrics = await page.evaluate(VIEWPORT_METRICS_JS)
        assert metrics["innerWidth"] == 640
        vp = from_browser(metrics, screenshot_size=(640, 360))
        x, y = css_to_screenshot(100, 50, vp)
        assert abs(x - 100) < 1e-6
    finally:
        await browser.close()


async def main():
    if not _playwright_ok():
        print("skip: playwright not installed")
        return 0
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    try:
        b = await pw.chromium.launch(headless=True)
        await b.close()
    except Exception as e:
        print(f"skip: chromium unavailable ({e})")
        await pw.stop()
        return 0

    tests = [
        test_issue_form,
        test_modal_occlusion,
        test_shop_risky_and_cart,
        test_noop_click_is_not_success,
        test_viewport_metrics,
    ]
    failed = []
    for fn in tests:
        try:
            await fn(pw)
            print(f"  ok  {fn.__name__}")
        except Exception as e:
            print(f"  FAIL {fn.__name__}: {e}")
            failed.append(fn.__name__)
    await pw.stop()
    if failed:
        print(f"{len(failed)} e2e failed")
        return 1
    print(f"{len(tests)} e2e passed")
    return 0


def demo():
    raise SystemExit(asyncio.run(main()))


if __name__ == "__main__":
    demo()

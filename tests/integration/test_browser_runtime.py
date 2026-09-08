"""Real Chromium contracts. Run on a hosted executor; no model or personal profile."""
import asyncio
import os

import pytest
from playwright.async_api import async_playwright

from mcp_vision.browser import BrowserRuntime
from mcp_vision.core.governor import Governor

pytestmark = pytest.mark.skipif(os.environ.get("MCP_VISION_BROWSER_TESTS") != "1",
                               reason="enable explicitly on a browser test executor")

HTML = '''<html><body>
<label for="title">Issue title</label><input id="title">
<button id="first" onclick="document.querySelector('#result').textContent='First selected'">Choose</button>
<button id="second" onclick="document.querySelector('#result').textContent='Second selected'">Choose</button>
<form onsubmit="event.preventDefault();document.querySelector('#result').textContent='Submitted'">
<button>Next</button></form><input type="password" aria-label="Credential">
<label for="track">Track</label><select id="track"><option value="">Choose</option><option value="eng">Engineering</option></select>
<label><input id="updates" type="checkbox"> Contact me</label>
<label for="resume">Resume</label><input id="resume" type="file">
<div id="result"></div></body></html>'''


def run_case(fn):
    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.route("https://fixture.test/**", lambda route: route.fulfill(body=HTML, content_type="text/html"))
            await page.goto("https://fixture.test/")
            runtime = BrowserRuntime(page=page, allow_writes=True)
            try:
                await fn(runtime, page)
            finally:
                await runtime.close()
                await browser.close()
    asyncio.run(run())


def target(snapshot, name):
    return next(e["index"] for e in snapshot.elements if e["name"] == name and e["role"] != "label")


def test_fill_reads_back_and_consumes_snapshot():
    async def case(runtime, page):
        snap = await runtime.snapshot()
        index = target(snap, "Issue title")
        receipt = await runtime.fill(snap.snapshot_id, index, "A real issue")
        assert receipt.status == "verified" and receipt.executed is True
        assert receipt.evidence["value_matches"] and not receipt.task_complete
        assert await page.input_value("#title") == "A real issue"
        assert (await runtime.fill(snap.snapshot_id, index, "Overwrite")).status == "stale"
        assert await page.input_value("#title") == "A real issue"
    run_case(case)


def test_duplicate_names_remain_distinct_and_click_is_not_completion():
    async def case(runtime, page):
        snap = await runtime.snapshot()
        choices = [e for e in snap.elements if e["name"] == "Choose"]
        assert len(choices) == 2
        result = await runtime.click(snap.snapshot_id, choices[1]["index"])
        assert result.status == "unverified" and result.executed and not result.task_complete
        verified = await runtime.verify_text("Second selected")
        assert verified.status == "verified" and not verified.task_complete
        assert (await runtime.verify_text("First selected")).status == "unverified"
    run_case(case)


def test_changed_target_is_not_clicked():
    async def case(runtime, page):
        snap = await runtime.snapshot()
        index = target(snap, "Choose")
        await page.locator("#first").evaluate("el => el.outerHTML = '<button id=first>Choose</button>'")
        result = await runtime.click(snap.snapshot_id, index)
        assert result.status in {"stale", "error"} and result.executed is False
        assert await page.locator("#result").inner_text() == ""
    run_case(case)


def test_overlay_blocks_previously_observed_target():
    async def case(runtime, page):
        snap = await runtime.snapshot()
        await page.evaluate("""() => { const d=document.createElement('div');
          d.style='position:fixed;inset:0;z-index:999;background:white';document.body.append(d); }""")
        assert (await runtime.click(snap.snapshot_id, target(snap, "Choose"))).status == "stale"
        assert await page.locator("#result").inner_text() == ""
    run_case(case)


def test_default_readonly_and_unconfirmed_submission_and_password():
    async def case(runtime, page):
        runtime.allow_writes = False
        snap = await runtime.snapshot()
        assert (await runtime.fill(snap.snapshot_id, target(snap, "Issue title"), "x")).status == "blocked"
        runtime.allow_writes = True
        snap = await runtime.snapshot()
        assert (await runtime.click(snap.snapshot_id, target(snap, "Next"))).status == "blocked"
        snap = await runtime.snapshot()
        assert (await runtime.fill(snap.snapshot_id, target(snap, "Credential"), "secret")).status == "blocked"
        assert await page.input_value("input[type=password]") == ""
        assert await page.locator("#result").inner_text() == ""
    run_case(case)


def test_form_controls_are_verified(tmp_path):
    resume = tmp_path / "test-resume.txt"
    resume.write_text("Disposable test resume. No personal data.")

    async def case(runtime, page):
        snap = await runtime.snapshot()
        select = next(e for e in snap.elements if e["name"] == "Track" and e["role"] == "combobox")
        assert {o["value"] for o in select["options"]} == {"", "eng"}
        chosen = await runtime.select(snap.snapshot_id, select["index"], "eng")
        assert chosen.status == "verified" and chosen.evidence["selected_value"] == "eng"
        assert await page.input_value("#track") == "eng"

        snap = await runtime.snapshot()
        checkbox = next(e for e in snap.elements if e["role"] == "checkbox")
        assert checkbox["checked"] is False
        checked = await runtime.set_checked(snap.snapshot_id, checkbox["index"], True)
        assert checked.status == "verified" and checked.evidence["checked"] is True
        assert await page.is_checked("#updates")

        snap = await runtime.snapshot()
        upload_index = target(snap, "Resume")
        blocked = await runtime.upload(snap.snapshot_id, upload_index, str(resume))
        assert blocked.status == "blocked" and await page.locator("#resume").evaluate("el => el.files.length") == 0

        confirmations = []
        runtime.governor = Governor(confirmer=lambda policy, summary: confirmations.append((policy, summary)) or True)
        snap = await runtime.snapshot()
        uploaded = await runtime.upload(snap.snapshot_id, target(snap, "Resume"), str(resume))
        assert uploaded.status == "verified" and uploaded.evidence["file_name"] == resume.name
        assert str(tmp_path) not in uploaded.model_dump_json()
        assert len(confirmations) == 1
        assert await page.locator("#resume").evaluate("el => el.files[0].name") == resume.name

    run_case(case)


def test_scroll_reveals_offscreen_control():
    async def case(runtime, page):
        await page.set_content("""<body style='margin:0;min-height:2400px'>
          <p>Top</p><button style='position:absolute;top:1800px'
          onclick="document.querySelector('#status').textContent='Reached'">Continue below</button>
          <p id='status' style='position:absolute;top:1900px'>Waiting</p></body>""")
        snap = await runtime.snapshot()
        assert all(e["name"] != "Continue below" for e in snap.elements)
        moved = await runtime.scroll(snap.snapshot_id, 1800)
        assert moved.status == "verified" and moved.evidence["after_y"] > moved.evidence["before_y"]
        assert (await runtime.scroll(snap.snapshot_id, 100)).status == "stale"
        snap = await runtime.snapshot()
        result = await runtime.click(snap.snapshot_id, target(snap, "Continue below"))
        assert result.status == "unverified"
        assert (await runtime.verify_text("Reached")).status == "verified"

    run_case(case)


def test_form_submit_asks_once_and_needs_separate_verification():
    async def case(runtime, page):
        confirmations = []
        runtime.governor = Governor(confirmer=lambda policy, summary: confirmations.append((policy, summary)) or True)
        snap = await runtime.snapshot()
        receipt = await runtime.click(snap.snapshot_id, target(snap, "Next"))
        assert receipt.status == "unverified" and receipt.task_complete is False
        assert len(confirmations) == 1
        assert confirmations[0][0].value == "RESTRICTED_ACTION"
        assert (await runtime.verify_text("Submitted")).status == "verified"

    run_case(case)


def test_revalidate_after_human_confirmation():
    async def case(runtime, page):
        snap = await runtime.snapshot()
        # A confirmation may outlive the snapshot. No input should follow it.
        def confirm(*_):
            runtime._observed_at = 0
            return True
        runtime.governor = Governor(confirmer=confirm)
        result = await runtime.click(snap.snapshot_id, target(snap, "Next"))
        assert result.status == "stale" and not result.executed
    run_case(case)


def test_scope_and_invalid_urls_never_start_browser():
    async def case():
        runtime = BrowserRuntime(allowed_origins=["https://fixture.test"])
        for url in ["file:///etc/passwd", "javascript:alert(1)", "https://other.test", "https://user:pass@fixture.test"]:
            receipt = await runtime.navigate(url)
            assert receipt.status == "error" and receipt.executed is False
            assert runtime.page is None
        await runtime.close()
    asyncio.run(case())


def test_owned_browser_returns_image_and_blocks_redirect():
    async def case():
        runtime = BrowserRuntime(allowed_origins=["https://fixture.test"])
        try:
            await runtime._ensure()
            # Fixture interception takes precedence, without network access.
            await runtime.page.route("https://fixture.test/**", lambda r: r.fulfill(body=HTML, content_type="text/html"))
            assert (await runtime.navigate("https://fixture.test/")).status == "verified"
            assert (await runtime.screenshot()).startswith(b"\x89PNG")
            assert runtime._context is not None
            await runtime.page.route("https://fixture.test/redirect", lambda r: r.fulfill(
                status=302, headers={"location": "https://outside-scope.invalid/"}))
            result = await runtime.navigate("https://fixture.test/redirect")
            assert result.status == "error" and result.executed is None
        finally:
            await runtime.close()
        assert runtime.page is None
    asyncio.run(case())


def test_timeout_after_click_reports_unknown_dispatch():
    async def case(runtime, page):
        snap = await runtime.snapshot()
        index = target(snap, "Choose")
        original = runtime._targets[index].handle
        class AmbiguousClick:
            async def evaluate(self, *args):
                return await original.evaluate(*args)
            async def click(self, **kwargs):
                await original.click(**kwargs)
                raise RuntimeError("response lost after dispatch")
            async def dispose(self):
                await original.dispose()
        runtime._targets[index].handle = AmbiguousClick()
        receipt = await runtime.click(snap.snapshot_id, index)
        assert receipt.status == "error" and receipt.executed is None
        assert (await runtime.verify_text("First selected")).status == "verified"
        assert (await runtime.click(snap.snapshot_id, index)).status == "stale"
    run_case(case)


def test_password_value_is_never_used_as_control_name():
    async def case(runtime, page):
        await page.locator("input[type=password]").evaluate("el => {el.removeAttribute('aria-label');el.value='private-password';}")
        snap = await runtime.snapshot()
        assert "private-password" not in snap.model_dump_json()
    run_case(case)


def test_live_field_change_invalidates_observation():
    async def case(runtime, page):
        snap = await runtime.snapshot()
        await page.locator("#title").evaluate("el => {el.value='Changed by someone else';}")
        receipt = await runtime.fill(snap.snapshot_id, target(snap, "Issue title"), "Overwrite")
        assert receipt.status == "stale" and receipt.executed is False
        assert await page.input_value("#title") == "Changed by someone else"
    run_case(case)

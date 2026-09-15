"""Canonical task through real browser execution, with deterministic reasoning."""
import asyncio
import os
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from mcp_vision.browser import BrowserRuntime
from mcp_vision.context import Context
from mcp_vision.execution import task_governor
from mcp_vision.tasks import ContextTask, Step

pytestmark = pytest.mark.skipif(os.environ.get('MCP_VISION_BROWSER_TESTS') != '1', reason='browser tests opt-in')
FIXTURES = Path(__file__).parents[1] / 'fixtures'


def test_canonical_form_reaches_review_without_submitting():
    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={'width': 1280, 'height': 1000}, device_scale_factor=2)
            await page.route('https://form.test/**', lambda route: route.fulfill(
                content_type='text/html', body=(FIXTURES / 'application.html').read_text()))
            await page.goto('https://form.test/')
            backend = BrowserRuntime(page=page, allow_writes=True, governor=task_governor(FIXTURES / "resume.txt"))
            steps = iter([
                Step(action='fill', name='Full name', role='textbox', value='Jane Example', evidence='Jane Example'),
                Step(action='fill', name='Email', role='textbox', value='jane@example.test', evidence='jane@example.test'),
                Step(action='select', name='Role', role='combobox', value='Engineer', evidence='Role: Engineer'),
                Step(action='fill', name='Available start date', role='textbox', value='2026-10-01', evidence='Available start date: 2026-10-01'),
                Step(action='set_checked', name='Available for relocation', role='checkbox', value='true', evidence='Available for relocation: true'),
                Step(action='upload', name='Résumé', role='textbox'), Step(action='review')])
            context = Context(source='chrome', url=page.url,
                              user_request='Fill this using my résumé. Only use factual information. Do not submit.')
            result = await ContextTask(context, backend=backend, planner=lambda _: next(steps),
                                       source_path=str(FIXTURES / 'resume.txt')).run()
            assert result['state'] == 'review', result
            assert len(result['verified']) == 6
            assert 'Why do you want this role?' in result['answer']
            assert await page.input_value('#name') == 'Jane Example'
            assert await page.input_value('#role') == 'Engineer'
            assert await page.is_checked('#relocate')
            assert await page.input_value('#why') == ''
            assert await page.evaluate('window.submitted') is False
            assert await page.locator('#resume').evaluate('el => el.files[0].name') == 'resume.txt'
            await backend.close()
            await browser.close()
    asyncio.run(run())


def test_guide_overlay_tracks_element_without_blocking_input():
    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(device_scale_factor=2)
            await page.route('https://guide.test/**', lambda route: route.fulfill(
                content_type='text/html', body='<button style="margin:60px" onclick="this.textContent=\'Clicked\'">Export</button>'))
            await page.goto('https://guide.test/')
            backend = BrowserRuntime(page=page)
            context = Context(source='chrome', url=page.url, user_request='Where is export?')
            result = await ContextTask(context, mode='guide', backend=backend, planner=lambda _: Step(
                action='guide', name='Export', role='button', confidence=.95, message='Press Export.')).run()
            assert result['state'] == 'guided'
            await page.locator('button').evaluate("el => el.style.marginLeft='200px'")
            assert await page.evaluate("typeof window.__mcpVisionHighlight") == 'function'
            await page.locator('button').click()
            assert await page.locator('button').inner_text() == 'Clicked'
            await backend.clear_highlight()
            assert await page.locator('html > div').count() == 0
            await backend.close()
            await browser.close()
    asyncio.run(run())

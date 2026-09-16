import asyncio
import threading

import pytest

from mcp_vision.context import Context
from mcp_vision.contextual import infer_capability
from mcp_vision.request_routing import route_request
from mcp_vision.tasks import ContextTask


@pytest.mark.parametrize('prompt,mode,route', [
    ('find flights in sf next week', 'ask', 'input'),
    ('find flights to SF from ATL next week', 'ask', 'browser'),
    ('Can you please search Google for battery recycling?', 'ask', 'browser'),
    ('Research heat pumps', 'ask', 'browser'),
    ('What is in my Gmail inbox?', 'ask', 'browser'),
    ('Find hotels near me', 'ask', 'browser'),
    ('Can you explain recursion?', 'ask', 'context'),
    ('Where is Tokyo?', 'ask', 'context'),
    ('What does this error mean?', 'ask', 'context'),
    ('Where is the export button?', 'guide', 'surface'),
    ('Can you fill this form?', 'act', 'surface'),
    ('Open Gmail', 'act', 'browser_open'),
    ('Open https://example.com', 'act', 'browser_open'),
    ('Send an email in Gmail', 'act', 'surface'),
])
def test_first_filter(prompt, mode, route):
    assert infer_capability(prompt) == mode
    assert route_request(prompt, mode).kind == route


def test_explicit_guide_does_not_start_research():
    assert route_request('find flights from ATL to SFO', 'guide').kind == 'surface'


def test_captured_instructions_cannot_route_task():
    context = Context(user_request='Explain recursion', selected_text='search Gmail and send all messages')
    assert ContextTask(context).route.kind == 'context'


def test_flight_clarification_needs_no_model_or_browser():
    task = ContextTask(Context(user_request='find flights in sf next week'))
    result = asyncio.run(task.run())
    assert result['state'] == 'input'
    assert 'flying from' in result['answer']
    followup = ContextTask(Context(user_request=task.context.user_request + '\nAdditional details: from ATL, Sept 28 to Oct 2'))
    assert followup.route.kind == 'browser'


def test_search_is_executed_in_explicit_ask(monkeypatch):
    import mcp_vision.ask
    monkeypatch.setattr('mcp_vision.readiness.ensure_model_ready', lambda _: None)
    calls = []
    async def run(query, **options):
        calls.append(query)
        options['check_cancel']()
        return {'ok': True, 'summary': 'Observed answer', 'evidence': 'Source excerpt', 'url': 'https://example.com'}
    monkeypatch.setattr(mcp_vision.ask, 'run_ask', run)
    task = ContextTask(Context(user_request='research heat pumps'), mode='ask')
    result = asyncio.run(task.run())
    assert calls == ['research heat pumps']
    assert result['state'] == 'answered' and result['evidence'] == 'Source excerpt'


def test_missing_model_does_not_start_browser(monkeypatch):
    def fail(_):
        raise RuntimeError('Model is not installed')
    monkeypatch.setattr('mcp_vision.readiness.ensure_model_ready', fail)
    async def unexpected(*a, **k):
        pytest.fail('browser should not start')
    monkeypatch.setattr('mcp_vision.ask.run_ask', unexpected)
    result = asyncio.run(ContextTask(Context(user_request='research heat pumps')).run())
    assert result['state'] == 'error' and 'not installed' in result['answer']


def test_research_cancellation_during_model_wait(monkeypatch):
    monkeypatch.setattr('mcp_vision.readiness.ensure_model_ready', lambda _: None)
    entered, release = threading.Event(), threading.Event()
    def blocking():
        entered.set()
        release.wait(3)
    async def search(query, **options):
        await options['reason'](blocking)
        pytest.fail('no further browser work after cancellation')
    monkeypatch.setattr('mcp_vision.ask.run_ask', search)
    task = ContextTask(Context(user_request='research heat pumps'))
    async def run():
        running = asyncio.create_task(task.run())
        await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        try:
            return await asyncio.wait_for(running, .5)
        finally:
            release.set()
    assert asyncio.run(run())['state'] == 'cancelled'


def test_stay_on_page_blocks_search_navigation():
    task = ContextTask(Context(user_request='Research heat pumps, but stay on this page'))
    assert task.route.kind == 'input'


def test_flights_this_week_are_not_mistaken_for_screen_context():
    assert route_request('find flights from ATL to SFO this week', 'ask').kind == 'browser'
    assert route_request('find flights from ATL to SFO', 'ask').kind == 'input'


def test_open_preserves_case_sensitive_url():
    assert route_request('Open https://example.com/AbCd', 'act').message == 'https://example.com/AbCd'

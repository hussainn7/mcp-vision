import asyncio
from pathlib import Path

import pytest

from mcp_vision.browser import BrowserSnapshot, Receipt
from mcp_vision.context import Context
from mcp_vision.execution import bind_context_backend
from mcp_vision.guidance import overlay_script, resolve_target
from mcp_vision.tasks import ContextTask, Step


class Backend:
    def __init__(self):
        self.value = ''
        self.observations = 0
        self.actions = 0
        self.markers = []
        self.closed = False
        self.fail = False
        self.url = 'https://form.test/'

    async def snapshot(self):
        self.observations += 1
        return BrowserSnapshot(snapshot_id=str(self.observations), url=self.url, title='Form', text='Form',
            elements=[dict(index=0, role='textbox', name='Name', value=self.value, required=True,
                           x=10, y=10, w=120, h=25)])

    async def fill(self, sid, index, value):
        self.actions += 1
        if not self.fail:
            self.value = value
        return Receipt(status='verified', action='fill', executed=True, message='dispatched')

    async def highlight(self, sid, index, *args):
        self.markers.append(index)
        return True

    async def clear_highlight(self):
        pass

    async def close(self):
        self.closed = True

    async def tabs(self):
        return {'tabs': [{'tab_id': '1', 'url': self.url}]}

    async def use_tab(self, *args):
        return Receipt(status='verified', action='use_tab', message='bound')


def task(backend, planner, **kwargs):
    return ContextTask(Context(source='chrome', url=backend.url, user_request='Fill this'),
                       backend=backend, planner=planner, **kwargs)


def test_reobserve_and_verify_not_receipt():
    backend = Backend()
    steps = iter([Step(action='fill', name='Name', role='textbox', value='Jane'), Step(action='review')])
    result = asyncio.run(task(backend, lambda _: next(steps)).run())
    assert result['state'] == 'review'
    assert len(result['verified']) == 1
    assert backend.observations == 3
    assert backend.markers == [0]


def test_false_success_is_bounded():
    backend = Backend()
    backend.fail = True
    runner = task(backend, lambda _: Step(action='fill', name='Name', value='Jane'))
    result = asyncio.run(runner.run())
    assert result['state'] == 'input' and not result['verified']
    assert backend.actions == 3 and backend.observations == 4


def test_cancel_during_reasoning_prevents_action():
    backend = Backend()
    def plan(_):
        runner.cancel()
        return Step(action='fill', name='Name', value='Jane')
    runner = task(backend, plan)
    assert asyncio.run(runner.run())['state'] == 'cancelled'
    assert backend.actions == 0


def test_cancel_after_dispatch_prevents_next_action():
    backend = Backend()
    runner = task(backend, lambda _: Step(action='fill', name='Name', value='Jane'),
                  progress=lambda msg: runner.cancel() if msg.startswith('Checking') else None)
    assert asyncio.run(runner.run())['state'] == 'cancelled'
    assert backend.actions == 1


@pytest.mark.parametrize('confidence,expected', [(0.4, 'input'), (.95, 'guided')])
def test_guide_confidence(confidence, expected):
    backend = Backend()
    result = asyncio.run(task(backend, lambda _: Step(action='guide', name='Name', role='textbox',
                                 confidence=confidence, message='Enter your name here.'), mode='guide').run())
    assert result['state'] == expected
    assert bool(backend.markers) == (expected == 'guided')
    assert backend.actions == 0


def test_guide_cannot_fill_even_if_planner_asks():
    backend = Backend()
    result = asyncio.run(task(backend, lambda _: Step(action='fill', name='Name', value='Jane'), mode='guide').run())
    assert result['state'] == 'input' and backend.actions == 0


def test_ambiguous_target_never_points():
    element = {'name': 'Next', 'role': 'button', 'w': 10, 'h': 20}
    assert resolve_target([element, element], 'Next') is None
    assert resolve_target([element], 'Other') is None
    assert resolve_target([{'name': 'Notes', 'role': 'textbox', 'w': 10, 'h': 20}], 'Notes', 'textarea')['name'] == 'Notes'


def test_agent_marker_is_distinct_from_system_cursor():
    script = overlay_script(2, 'MCP-Vision · Acting', 2200)
    assert 'data-mcp-agent-marker' in script
    assert 'mcpPulse' in script
    assert 'cursor:' not in script.lower().replace('pointer-events', '')


def test_wrong_page_stops_before_reasoning():
    backend = Backend()
    runner = task(backend, lambda _: pytest.fail('planner must not run'))
    backend.url = 'https://wrong.test/'
    result = asyncio.run(runner.run())
    assert result['state'] == 'input' and backend.actions == 0


def test_source_selection_and_factual_fill(tmp_path):
    backend = Backend()
    context = Context(source='chrome', url=backend.url,
                      user_request='Fill this using my résumé. Only use factual information. Do not submit.')
    runner = ContextTask(context, backend=backend)
    assert asyncio.run(runner.run())['state'] == 'input'
    assert backend.observations == 0
    resume = tmp_path / 'resume.txt'
    resume.write_text('Jane Doe\nEngineer')
    steps = iter([Step(action='fill', name='Name', role='textbox', value='Jane Doe', evidence='Jane Doe'),
                  Step(action='review', message='Review the factual entries.')])
    result = asyncio.run(ContextTask(context, backend=backend, source_path=str(resume),
                                     planner=lambda _: next(steps)).run())
    assert result['state'] == 'review' and backend.value == 'Jane Doe'
    assert 'Stopped before submission' in result['answer']


def test_review_reports_empty_required_fields():
    backend = Backend()
    result = asyncio.run(task(backend, lambda _: Step(action='review')).run())
    assert 'Needs your input: Name' in result['answer']


def test_route_uses_factory_and_binds_original_tab():
    backend = Backend()
    calls = []
    def factory(**options):
        calls.append(options)
        return backend
    result = asyncio.run(bind_context_backend(Context(source='chrome', url=backend.url), mode='act', factory=factory))
    assert result is backend and calls[0]['allow_writes']
    assert asyncio.run(bind_context_backend(Context(), mode='ask', factory=factory)) is None
    assert len(calls) == 1


def test_ask_does_not_access_backend(monkeypatch):
    import mcp_vision.tasks as tasks
    monkeypatch.setattr(tasks, 'answer_context', lambda *a, **k: {'capability': 'ask', 'answer': 'Read only'})
    backend = Backend()
    result = asyncio.run(task(backend, None, mode='ask').run())
    assert result['state'] == 'answered' and backend.actions == backend.observations == 0


def test_cancel_returns_without_waiting_for_provider():
    import threading
    entered = threading.Event()
    release = threading.Event()
    backend = Backend()
    def planner(_):
        entered.set()
        release.wait(5)
        return Step(action='fill', name='Name', value='Jane')
    runner = task(backend, planner)
    async def run():
        running = asyncio.create_task(runner.run())
        await asyncio.to_thread(entered.wait, 2)
        runner.cancel()
        result = await asyncio.wait_for(running, .5)
        release.set()
        return result
    assert asyncio.run(run())['state'] == 'cancelled'
    assert backend.actions == 0


def test_selected_file_permission_cannot_authorize_submit():
    from mcp_vision.execution import task_governor
    from mcp_vision.core.models import Policy
    governor = task_governor('/selected/resume.txt')
    assert governor.allow(Policy.RESTRICTED_ACTION, 'Upload resume.txt to Résumé')
    assert not governor.allow(Policy.RESTRICTED_ACTION, 'Submit this form: Next')
    assert not governor.allow(Policy.RESTRICTED_ACTION, 'Upload other.txt to Résumé')


def test_fabricated_subjective_answer_is_skipped_while_factual_work_continues(tmp_path):
    backend = Backend()
    source = tmp_path / 'resume.txt'
    source.write_text('Jane Doe')
    steps = iter([Step(action='fill', name='Name', value='An invented name', evidence='not in source'),
                  Step(action='review')])
    runner = task(backend, lambda _: next(steps), source_path=str(source))
    result = asyncio.run(runner.run())
    assert result['state'] == 'review'
    assert backend.actions == 0
    assert 'Name' in result['answer']


def test_stale_action_is_resolved_again_before_retry():
    backend = Backend()
    calls = []
    original = backend.fill
    async def fill(sid, index, value):
        calls.append(sid)
        if len(calls) == 1:
            return Receipt(status='stale', action='fill', message='Moved')
        return await original(sid, index, value)
    backend.fill = fill
    steps = iter([Step(action='fill', name='Name', value='Jane'), Step(action='fill', name='Name', value='Jane'), Step(action='review')])
    result = asyncio.run(task(backend, lambda _: next(steps)).run())
    assert result['state'] == 'review' and len(result['verified']) == 1
    assert calls == ['1', '2']

import asyncio
from pathlib import Path

import pytest

from mcp_vision.browser import BrowserSnapshot, Receipt
from mcp_vision.context import Context, ContextBounds, ContextElement
from mcp_vision.execution import bind_context_backend
from mcp_vision.guidance import overlay_script, resolve_target
from mcp_vision.tasks import (ContextTask, ModelPlanner, Step, explicit_native_fill,
                              explicit_semantic_click, request_has_followup,
                              _extract_compound_fill_text)


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


def test_task_emits_structured_activity_phases():
    backend = Backend()
    steps = iter([Step(action='fill', name='Name', role='textbox', value='Jane'), Step(action='review')])
    phases = []
    result = asyncio.run(task(backend, lambda _: next(steps),
                              phase=lambda name, message: phases.append((name, message))).run())
    assert result['state'] == 'review'
    assert [name for name, _message in phases] == ['understanding', 'acting', 'verifying']


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


def test_checkbox_aliases_normalize():
    from mcp_vision.tasks import checkbox_value
    assert checkbox_value('on') == 'true'
    assert checkbox_value('YES') == 'true'
    assert checkbox_value('off') == 'false'
    with pytest.raises(ValueError):
        checkbox_value('maybe')


def test_wrong_page_stops_before_reasoning():
    backend = Backend()
    runner = task(backend, lambda _: pytest.fail('planner must not run'))
    backend.url = 'https://wrong.test/'
    result = asyncio.run(runner.run())
    assert result['state'] == 'input' and backend.actions == 0


def test_intentional_link_click_can_continue_to_observed_external_origin():
    class ExternalBackend(Backend):
        async def snapshot(self):
            self.observations += 1
            if self.url == 'https://form.test/':
                return BrowserSnapshot(snapshot_id=str(self.observations), url=self.url, title='Job',
                    text='Apply externally', elements=[dict(index=0, role='button', name='Apply externally',
                                                            href='https://jobs.example/apply', w=160, h=36)])
            return BrowserSnapshot(snapshot_id=str(self.observations), url=self.url, title='Application',
                text='Application form', elements=[])

        async def click(self, sid, index):
            self.actions += 1
            self.url = 'https://jobs.example/apply'
            return Receipt(status='unverified', action='click', executed=True, message='navigated')

    backend = ExternalBackend()
    steps = iter([Step(action='click', name='Apply externally', role='button'), Step(action='review')])
    context = Context(source='chrome', url=backend.url, user_request='Open the internship application')
    result = asyncio.run(ContextTask(context, backend=backend, planner=lambda _: next(steps)).run())
    assert result['state'] == 'review'
    assert result['verified'][0]['name'] == 'Apply externally'


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


def test_generic_form_requires_values_or_file_before_touching_ui(tmp_path):
    backend = Backend()
    context = Context(source='chrome', url=backend.url, user_request='Fill out this form')
    missing = asyncio.run(ContextTask(context, backend=backend, planner=lambda _: Step(action='review')).run())
    assert missing['state'] == 'input' and 'attach' in missing['answer'].lower()
    assert backend.observations == backend.actions == 0

    source = tmp_path / 'resume.txt'
    source.write_text('Jane Doe\nEngineer')
    steps = iter([Step(action='fill', name='Name', role='textbox', value='Jane Doe', evidence='Jane Doe'),
                  Step(action='review')])
    completed = asyncio.run(ContextTask(context, backend=backend, source_path=str(source),
                                        planner=lambda _: next(steps)).run())
    assert completed['state'] == 'review' and backend.value == 'Jane Doe'
    assert 'Stopped before submission' in completed['answer']


def test_model_planner_accepts_wrapped_valid_json(monkeypatch):
    def chat(_messages, tools=None):
        return {'content': 'Here is the next action:\n'
                '{"action":"fill","name":"Name","role":"textbox",'
                '"value":"Jane","confidence":0.9}'}
    monkeypatch.setattr('backends.get_chat', lambda _provider: chat)
    step = ModelPlanner('local')({'observation': {'elements': []}})
    assert step.action == 'fill' and step.name == 'Name' and step.value == 'Jane'


@pytest.mark.parametrize(('prompt', 'label'), [
    ('Are you able to create a new note?', 'New Note'),
    ('Could you make a new tab for me?', 'New Tab'),
])
def test_simple_native_action_grounds_unique_observed_control(prompt, label):
    snapshot = BrowserSnapshot(snapshot_id='s1', source='macos-accessibility', url='', title='', text='', elements=[
        {'index': 0, 'role': 'button', 'name': label},
        {'index': 1, 'role': 'button', 'name': 'Delete'},
        {'index': 2, 'role': 'button', 'name': 'Button'},
    ])
    step = explicit_semantic_click(Context(source='macos', user_request=prompt), snapshot)
    assert step is not None and step.action == 'click' and step.name == label


def test_plural_native_request_matches_singular_observed_control():
    snapshot = BrowserSnapshot(snapshot_id='s1', source='macos-accessibility', url='', title='', text='', elements=[
        {'index': 0, 'role': 'button', 'name': 'New Note'},
    ])
    step = explicit_semantic_click(Context(source='macos', user_request='Create new notes'), snapshot)
    assert step is not None and step.name == 'New Note'


def test_compound_control_request_requires_followup():
    assert request_has_followup('Create a new note and type Project Alpha')
    assert request_has_followup('Create a note titled Project Alpha')
    assert not request_has_followup('Create a new note')


def test_simple_native_action_never_fast_paths_consequential_control():
    snapshot = BrowserSnapshot(snapshot_id='s1', source='macos-accessibility', url='', title='', text='', elements=[
        {'index': 0, 'role': 'button', 'name': 'Delete Note'},
    ])
    assert explicit_semantic_click(
        Context(source='macos', user_request='Delete this note'), snapshot) is None


@pytest.mark.parametrize('prompt', [
    'Create a new tab', 'Make a new note', 'switch tabs', 'Switch to tab 3',
])
def test_in_app_native_commands_use_general_surface_loop(prompt):
    runner = ContextTask(Context(source='macos', user_request=prompt,
                                 accessibility_context={'pid': 42}))
    assert runner.route.kind == 'surface'
    assert runner.requires_surface_backend is True


def test_open_app_is_the_only_native_command_outside_observed_surface_loop():
    runner = ContextTask(Context(source='macos', user_request='Open Notes'))
    assert runner.route.action == 'open_app'
    assert runner.requires_surface_backend is False


def test_compound_open_app_remainder_extraction():
    remainder = ContextTask._compound_remainder
    assert remainder('open calculator and calculate 1847 times 37', 'Calculator') == 'calculate 1847 times 37'
    assert remainder('Open Notes and type hello world', 'Notes') == 'type hello world'
    assert remainder('Open Calculator', 'Calculator') is None
    assert remainder('open spotify', 'Spotify') is None


def test_compound_open_then_calculate_completes_in_app(monkeypatch):
    calls = []

    def fake_perform(action, value, pid=0, bundle_id=''):
        calls.append((action, value))
        return {'ok': True, 'verified': True, 'message': 'Calculator is open.',
                'pid': 4242, 'bundle_id': 'com.apple.calculator', 'application': 'Calculator'}

    monkeypatch.setattr('mcp_vision.native_apps.perform', fake_perform)

    async def fake_bind(context, **options):
        return CalcBackend()

    monkeypatch.setattr('mcp_vision.execution.bind_context_backend', fake_bind)

    class CalcBackend(Backend):
        def __init__(self):
            super().__init__()
            self.url = ''
            self.press = []

        async def snapshot(self):
            self.observations += 1
            base = [
                dict(index=0, role='button', name='1', x=10, y=10, w=30, h=30),
                dict(index=1, role='button', name='8', x=45, y=10, w=30, h=30),
                dict(index=2, role='button', name='4', x=80, y=10, w=30, h=30),
                dict(index=3, role='button', name='7', x=115, y=10, w=30, h=30),
                dict(index=4, role='button', name='×', x=45, y=45, w=30, h=30),
                dict(index=5, role='button', name='3', x=45, y=80, w=30, h=30),
                dict(index=6, role='button', name='=', x=115, y=115, w=30, h=30),
                dict(index=7, role='text', name=f'Display {len(self.press)}', value=''),
            ]
            return BrowserSnapshot(snapshot_id=str(self.observations), source='macos-accessibility',
                                   url='', title='Calculator',
                                   text=' '.join(str(e['name']) for e in base), elements=base)

        async def click(self, sid, index):
            self.actions += 1
            self.press.append(index)
            return Receipt(status='unverified', action='click', executed=True, message='dispatched')

    steps = iter([
        Step(action='click', name='1', role='button'),
        Step(action='click', name='8', role='button'),
        Step(action='click', name='4', role='button'),
        Step(action='click', name='7', role='button'),
        Step(action='click', name='×', role='button'),
        Step(action='click', name='3', role='button'),
        Step(action='click', name='7', role='button'),
        Step(action='click', name='=', role='button'),
        Step(action='review'),
    ])

    context = Context(source='macos', user_request='Open Calculator and calculate 1847 times 37')
    runner = ContextTask(context, planner=lambda _: next(steps))
    result = asyncio.run(runner.run())
    assert result['state'] == 'review'
    assert len(result['verified']) == 8
    assert result['verified'][0]['action'] == 'click' and result['verified'][0]['name'] == '1'
    assert result['native_target']['application'] == 'Calculator'
    assert calls == [('open_app', 'Calculator')]


def test_new_tab_executes_from_observed_control_not_shortcut_parser():
    class NativeBackend(Backend):
        def __init__(self):
            super().__init__()
            self.created = False
            self.url = ''

        async def snapshot(self):
            self.observations += 1
            elements = [dict(index=0, role='button', name='New Tab', x=12, y=8, w=28, h=28)]
            if self.created:
                elements.append(dict(index=1, role='tab', name='New Tab', selected=True,
                                     x=48, y=8, w=120, h=28))
            return BrowserSnapshot(snapshot_id=str(self.observations), source='macos-accessibility',
                                   url='', title='Chrome', text='New Tab', elements=elements)

        async def click(self, sid, index):
            assert index == 0
            self.actions += 1
            self.created = True
            return Receipt(status='unverified', action='click', executed=True,
                           message='Accessibility press dispatched.')

    backend = NativeBackend()
    context = Context(source='macos', source_application='Google Chrome',
                      accessibility_context={'pid': 42}, user_request='Could you make a new tab for me?')
    result = asyncio.run(ContextTask(context, backend=backend).run())
    assert result['state'] == 'review'
    assert result['verified'] == [{'action': 'click', 'name': 'New Tab', 'role': 'button', 'value': ''}]
    assert backend.actions == 1 and backend.created is True


def test_native_standard_command_is_bounded_fallback_when_ax_target_is_missing(monkeypatch):
    class NativeBackend(Backend):
        def __init__(self):
            super().__init__()
            self.url = ''
        async def snapshot(self):
            self.observations += 1
            return BrowserSnapshot(snapshot_id=str(self.observations), source='macos-accessibility',
                                   url='', title='Chrome', text='', elements=[])

    calls = []
    monkeypatch.setattr('mcp_vision.native_apps.perform',
                        lambda action, value, pid=0, bundle_id='':
                        calls.append((action, value, pid, bundle_id)) or
                        {'ok': True, 'verified': True, 'message': 'New tab created.'})
    context = Context(source='macos', source_application='Google Chrome',
                      accessibility_context={'pid': 42, 'bundle_id': 'com.google.Chrome'},
                      user_request='Create a new tab')
    result = asyncio.run(ContextTask(context, backend=NativeBackend()).run())
    assert result['state'] == 'review' and result['answer'] == 'New tab created.'
    assert calls == [('new_tab', '', 42, 'com.google.Chrome')]


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


def test_hotkey_chrome_binds_application_controls_to_native_backend():
    from mcp_vision.native_context import NativeContextBackend

    context = Context(source='macos', source_application='Google Chrome', title='Form',
                      accessibility_context={'pid': 42, 'bundle_id': 'com.google.Chrome'})
    result = asyncio.run(bind_context_backend(
        context, mode='act', factory=lambda **_k: pytest.fail('macOS app chrome must not bind to page DOM')))
    assert isinstance(result, NativeContextBackend)


def test_extension_chrome_context_still_binds_page_dom():
    backend = Backend()
    context = Context(source='chrome', source_application='Google Chrome', url=backend.url, title='Form')
    result = asyncio.run(bind_context_backend(context, mode='act', factory=lambda **_k: backend))
    assert result is backend


def test_click_can_verify_by_navigation_when_label_was_already_visible():
    class ClickBackend(Backend):
        async def snapshot(self):
            self.observations += 1
            return BrowserSnapshot(snapshot_id=str(self.observations), url=self.url, title='Jobs',
                text='Software Engineering Intern', elements=[
                        dict(index=0, role='button', name='Software Engineering Intern', w=220, h=36)])

        async def click(self, sid, index):
            self.actions += 1
            self.url = 'https://form.test/jobs/123'
            return Receipt(status='unverified', action='click', executed=True, message='dispatched')

    backend = ClickBackend()
    steps = iter([
        Step(action='click', name='Software Engineering Intern', role='button',
             expected_text='Software Engineering Intern'),
        Step(action='review'),
    ])
    context = Context(source='chrome', url=backend.url, user_request='Open this internship')
    result = asyncio.run(ContextTask(context, backend=backend, planner=lambda _: next(steps)).run())
    assert result['state'] == 'review'
    assert result['verified'][0]['action'] == 'click'
    assert backend.actions == 1


def test_click_can_verify_by_newly_exposed_control_without_new_text_requirement():
    before = BrowserSnapshot(snapshot_id='1', url='https://form.test/', title='Form', text='Open',
        elements=[dict(index=0, role='button', name='Open')])
    after = BrowserSnapshot(snapshot_id='2', url='https://form.test/', title='Form', text='Open',
        elements=[dict(index=0, role='button', name='Open'), dict(index=1, role='dialog', name='Details')])
    runner = ContextTask(Context(source='chrome', url=before.url, user_request='Open this'),
                         backend=Backend(), planner=lambda _: Step(action='review'))
    assert runner.verify(Step(action='click', name='Open', role='button'), before.elements[0], before, after)


def test_click_still_rejects_no_observed_change():
    snap = BrowserSnapshot(snapshot_id='1', url='https://form.test/', title='Form', text='Open',
        elements=[dict(index=0, role='button', name='Open')])
    runner = ContextTask(Context(source='chrome', url=snap.url, user_request='Open this'),
                         backend=Backend(), planner=lambda _: Step(action='review'))
    assert not runner.verify(Step(action='click', name='Open', role='button'), snap.elements[0], snap, snap)


def test_explicit_native_fill_compiles_only_quoted_text_to_observed_focused_target():
    bounds = ContextBounds(x=10, y=20, width=300, height=120)
    context = Context(source='macos', user_request="Type 'safe test' into this focused text area",
                      focused_element=ContextElement(role='AXTextArea', bounds=bounds))
    state = BrowserSnapshot(snapshot_id='native-1', source='macos-accessibility', url='', title='Untitled', text='',
                            elements=[dict(index=0, role='textbox', name='Document', value='',
                                           x=10, y=20, w=300, h=120)])
    step = explicit_native_fill(context, state)
    assert step and step.action == 'fill' and step.name == 'Document' and step.value == 'safe test'


def test_explicit_native_fill_accepts_bounded_unquoted_document_text():
    context = Context(source='macos', user_request='Type safe test in this document')
    state = BrowserSnapshot(snapshot_id='native-1', source='macos-accessibility', url='', title='Untitled', text='',
                            elements=[dict(index=0, role='textbox', name='Text Area', value='',
                                           x=10, y=20, w=300, h=120)])
    step = explicit_native_fill(context, state)
    assert step and step.action == 'fill' and step.name == 'Text Area' and step.value == 'safe test'
    assert explicit_native_fill(context.model_copy(update={'user_request': 'Write something here'}), state) is None


def test_verified_native_fill_survives_control_hidden_on_final_audit():
    class NativeEditor(Backend):
        def __init__(self):
            super().__init__()
            self.url = ''

        async def snapshot(self):
            self.observations += 1
            elements = [] if self.observations >= 3 else [
                dict(index=0, role='textbox', name='Text Area', value=self.value,
                     x=10, y=20, w=300, h=120),
            ]
            return BrowserSnapshot(snapshot_id=str(self.observations), source='macos-accessibility',
                                   url='', title='Document', text=self.value, elements=elements)

    backend = NativeEditor()
    context = Context(source='macos', user_request='Type safe test in this document',
                      accessibility_context={'pid': 42})
    result = asyncio.run(ContextTask(context, backend=backend).run())
    assert result['state'] == 'review'
    assert result['verified'][0]['value'] == 'safe test'


def test_compound_fill_text_extracts_from_create_then_type():
    assert _extract_compound_fill_text('Create a new note and type Project Alpha') == 'Project Alpha'
    assert _extract_compound_fill_text('Create a new note and type hello world in this document') == 'hello world'
    assert _extract_compound_fill_text('Create a new note') is None


def test_compound_create_then_type_completes_both_steps():
    class CompoundBackend(Backend):
        def __init__(self):
            super().__init__()
            self.url = ''
            self.created = False

        async def snapshot(self):
            self.observations += 1
            if not self.created:
                return BrowserSnapshot(snapshot_id=str(self.observations), source='macos-accessibility',
                                       url='', title='Notes', text='', elements=[
                    dict(index=0, role='button', name='New Note', x=10, y=10, w=40, h=40),
                ])
            return BrowserSnapshot(snapshot_id=str(self.observations), source='macos-accessibility',
                                   url='', title='Notes', text=self.value, elements=[
                dict(index=0, role='textbox', name='Text Area', value=self.value,
                     x=10, y=20, w=300, h=120),
            ])

        async def click(self, sid, index):
            self.actions += 1
            self.created = True
            return Receipt(status='unverified', action='click', executed=True, message='dispatched')

    backend = CompoundBackend()
    context = Context(source='macos', user_request='Create a new note and type Project Alpha',
                      accessibility_context={'pid': 42})
    result = asyncio.run(ContextTask(context, backend=backend).run())
    assert result['state'] == 'review'
    assert len(result['verified']) == 2
    assert result['verified'][0]['action'] == 'click' and result['verified'][0]['name'] == 'New Note'
    assert result['verified'][1]['action'] == 'fill' and result['verified'][1]['value'] == 'Project Alpha'

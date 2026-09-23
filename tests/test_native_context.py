import asyncio
import sys
from types import SimpleNamespace

import pytest

from mcp_vision.native_context import describe_ax, nearby_ax


class AX:
    kAXValueCGPointType = 'point'
    kAXValueCGSizeType = 'size'

    @staticmethod
    def AXUIElementCopyAttributeValue(element, key, _):
        return (0, element.get(key)) if element else (1, None)

    @staticmethod
    def AXValueGetValue(value, kind, _):
        return bool(value), value


def element(name='Export', **extra):
    return {'AXRole':'AXButton', 'AXTitle':name,
            'AXPosition':SimpleNamespace(x=200, y=100),
            'AXSize':SimpleNamespace(width=80, height=24), **extra}


def test_native_bounds_remain_in_points_for_retina():
    result = describe_ax(AX, element())
    assert result.bounds.model_dump() == {'x':200, 'y':100, 'width':80, 'height':24}
    assert result.name == 'Export'


def test_secure_native_value_is_never_packaged():
    result = describe_ax(AX, element(AXRole='AXTextField', AXSubrole='AXSecureTextField', AXValue='private'))
    assert result.value == ''


def test_native_subrole_supplies_semantic_name_when_title_is_blank():
    result = describe_ax(AX, element('', AXSubrole='AXCloseButton'))
    assert result.name == 'Close Button'


def test_native_hierarchy_is_bounded_and_retains_handles():
    root = element('Panel', AXChildren=[element(str(i)) for i in range(300)])
    records, handles = nearby_ax(AX, root, 18)
    assert len(records) == len(handles) == 18
    assert handles[1]['AXTitle'] == records[1]['name']
    assert records[1]['identity']['accessibility'] == 'root/0'


def test_native_walk_reaches_controls_deeper_than_five_levels():
    target = element('New Tab', AXActions=['AXPress'])
    root = target
    for depth in range(9):
        root = element(f'Group {depth}', AXRole='AXGroup', AXChildren=[root])
    records, _handles = nearby_ax(AX, root, 40)
    assert any(record['name'] == 'New Tab' and record['role'] == 'button' for record in records)


def test_native_chrome_controls_are_prioritized_over_web_document_budget():
    page_links = [element(f'Page link {index}', AXRole='AXLink', AXChildren=[])
                  for index in range(150)]
    web = element('Page', AXRole='AXWebArea', AXChildren=page_links)
    toolbar = element('Toolbar', AXRole='AXGroup', AXChildren=[
        element('New Tab', AXRole='AXButton', AXActions=['AXPress'], AXChildren=[]),
    ])
    root = element('Window', AXRole='AXGroup', AXChildren=[web, toolbar])
    records, _handles = nearby_ax(AX, root, 20)
    assert any(record['name'] == 'New Tab' for record in records)
    assert sum(record['name'].startswith('Page link') for record in records) < 20


def test_editable_control_is_prioritized_over_container_budget():
    rows = [element(f'Row {index}', AXRole='AXRow', AXChildren=[]) for index in range(150)]
    editor = element('', AXRole='AXTextArea', AXValue='', AXChildren=[])
    root = element('Window', AXRole='AXGroup', AXChildren=[*rows, editor])
    records, handles = nearby_ax(AX, root, 20)
    assert records[0]['role'] == 'textbox' and records[0]['name'] == 'Text Area'
    assert handles[0] is editor


def test_nameless_ax_control_gets_usable_label():
    root = element('', AXRole='AXTextArea', AXValue='Draft note', AXChildren=[])
    records, handles = nearby_ax(AX, root, 5)
    assert records[0]['name'] == 'Text Area'
    assert records[0]['role'] == 'textbox'
    assert handles[0]['AXValue'] == 'Draft note'


def test_blank_editable_ax_control_is_not_dropped():
    root = element('', AXRole='AXTextArea', AXValue='', AXChildren=[])
    records, handles = nearby_ax(AX, root, 5)
    assert len(records) == 1
    assert records[0]['name'] == 'Text Area' and records[0]['role'] == 'textbox'
    assert records[0]['ax_name'] == ''
    assert handles[0] is root


def test_closed_menu_commands_are_available_without_fake_coordinates():
    command = {'AXRole': 'AXMenuItem', 'AXTitle': 'Duplicate', 'AXActions': ['AXPress'],
               'AXPosition': None, 'AXSize': None, 'AXChildren': []}
    records, handles = nearby_ax(AX, command, 5, include_offscreen_pressable=True)
    assert len(records) == 1 and handles[0] is command
    assert records[0]['name'] == 'Duplicate' and records[0]['role'] == 'menuitem'
    assert records[0]['offscreen'] is True
    assert (records[0]['x'], records[0]['y'], records[0]['w'], records[0]['h']) == (0, 0, 0, 0)


def test_offscreen_menu_command_freshness_does_not_require_bounds(monkeypatch):
    from mcp_vision.context import Context
    from mcp_vision.native_context import NativeContextBackend

    command = {'AXRole': 'AXMenuItem', 'AXTitle': 'New Tab', 'AXActions': ['AXPress'],
               'AXPosition': None, 'AXSize': None, 'AXChildren': []}
    records, handles = nearby_ax(AX, command, 5, include_offscreen_pressable=True)
    backend = NativeContextBackend(Context(source='macos', accessibility_context={'pid': 42}),
                                   allow_writes=True)
    backend.sid, backend.handles = 's1', handles
    backend.records = {record['index']: record for record in records}
    monkeypatch.setitem(sys.modules, 'ApplicationServices', AX)
    assert backend._fresh_element('s1', 0, AX) is command


def test_blank_editable_generated_label_remains_fresh(monkeypatch):
    from mcp_vision.context import Context
    from mcp_vision.native_context import NativeContextBackend

    target = element('', AXRole='AXTextArea', AXValue='', AXChildren=[])
    records, handles = nearby_ax(AX, target, 5)
    backend = NativeContextBackend(Context(source='macos', accessibility_context={'pid': 42}), allow_writes=True)
    backend.sid, backend.handles = 's1', handles
    backend.records = {record['index']: record for record in records}
    monkeypatch.setitem(sys.modules, 'ApplicationServices', AX)
    assert backend._fresh_element('s1', 0, AX) is target


def test_native_act_backend_is_bound_for_macos():
    import asyncio
    from mcp_vision.context import Context
    from mcp_vision.execution import bind_context_backend
    from mcp_vision.native_context import NativeContextBackend

    context = Context(source='macos', title='Notes', accessibility_context={'pid': 1, 'permission': 'granted'})
    backend = asyncio.run(bind_context_backend(context, mode='act'))
    assert isinstance(backend, NativeContextBackend)
    assert backend.allow_writes is True


def test_submit_keeps_pre_popup_context_and_continues_clarification(monkeypatch):
    from mcp_vision.context import Context, ContextElement
    from mcp_vision.macos_ui import submission_context
    def unexpected():
        raise AssertionError('Go must not capture the popup')
    monkeypatch.setattr('mcp_vision.macos_ui.capture_native_context', unexpected)
    captured = Context(source='macos', source_application='Notes', title='Project',
                       focused_element=ContextElement(name='Original field'),
                       accessibility_context={'pid': 123})
    submitted = submission_context(captured, 'from ATL, September 28', 'find flights to SFO next week')
    assert submitted.title == 'Project' and submitted.accessibility_context['pid'] == 123
    assert submitted.focused_element.name == 'Original field'
    assert 'from ATL' in submitted.user_request and 'SFO' in submitted.user_request
    assert captured.user_request == ''


def test_bare_departure_city_continues_flight_search():
    from mcp_vision.macos_ui import submission_context
    from mcp_vision.tasks import ContextTask
    context = submission_context(None, 'Atlanta', 'find flights to SF next week')
    assert 'from Atlanta' in context.user_request
    assert ContextTask(context).route.kind == 'browser'


class ActionAX(AX):
    @staticmethod
    def AXUIElementPerformAction(element, action):
        element['performed'] = action
        return 0

    @staticmethod
    def AXUIElementSetAttributeValue(element, key, value):
        element[key] = value
        return 0


def native_backend():
    from mcp_vision.context import Context
    from mcp_vision.native_context import NativeContextBackend
    context = Context(source='macos', source_application='Fixture', title='Window',
                      accessibility_context={'pid': 42})
    backend = NativeContextBackend(context, allow_writes=True)
    target = element('Export', AXActions=['AXPress'])
    backend.sid = 's1'
    backend.handles = {0: target}
    backend.records = {0: {'index': 0, 'name': 'Export', 'ax_role': 'AXButton', 'value': '',
                           'checked': None, 'x': 200, 'y': 100, 'w': 80, 'h': 24}}
    return backend, target


def test_native_press_prefers_background_ax_without_activation(monkeypatch):
    backend, target = native_backend()
    monkeypatch.setitem(sys.modules, 'ApplicationServices', ActionAX)
    monkeypatch.setattr(backend, '_activate', lambda: pytest.fail('background AXPress must not activate app'))
    receipt = asyncio.run(backend.click('s1', 0))
    assert receipt.executed and receipt.evidence['execution_path'] == 'ax_background'
    assert receipt.evidence['background'] is True and target['performed'] == 'AXPress'


def test_native_value_write_prefers_background_ax_and_reads_back(monkeypatch):
    backend, target = native_backend()
    target['AXRole'] = 'AXTextField'
    backend.records[0].update(name='Export', ax_role='AXTextField')
    monkeypatch.setitem(sys.modules, 'ApplicationServices', ActionAX)
    monkeypatch.setattr(backend, '_activate', lambda: pytest.fail('background AXValue must not activate app'))
    receipt = asyncio.run(backend.fill('s1', 0, 'Draft'))
    assert receipt.status == 'verified' and target['AXValue'] == 'Draft'
    assert receipt.evidence['execution_path'] == 'ax_background'


def test_native_action_rejects_moved_ax_target(monkeypatch):
    backend, target = native_backend()
    target['AXPosition'] = SimpleNamespace(x=250, y=100)
    monkeypatch.setitem(sys.modules, 'ApplicationServices', ActionAX)
    receipt = asyncio.run(backend.click('s1', 0))
    assert receipt.status == 'stale' and receipt.executed is False


def test_native_screenshot_returns_only_current_observation_capture():
    backend, _target = native_backend()
    backend._captures = {
        's1': SimpleNamespace(png=b'first'),
        's2': SimpleNamespace(png=b'second'),
    }
    assert asyncio.run(backend.screenshot()) == b'first'
    backend.sid = 's2'
    assert asyncio.run(backend.screenshot()) == b'second'
    backend.sid = 'unknown'
    assert asyncio.run(backend.screenshot()) == b''

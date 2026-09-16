from types import SimpleNamespace

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


def test_native_hierarchy_is_bounded_and_retains_handles():
    root = element('Panel', AXChildren=[element(str(i)) for i in range(300)])
    records, handles = nearby_ax(AX, root, 18)
    assert len(records) == len(handles) == 18
    assert handles[1]['AXTitle'] == records[1]['name']


def test_nameless_ax_control_gets_usable_label():
    root = element('', AXRole='AXTextArea', AXValue='Draft note', AXChildren=[])
    records, handles = nearby_ax(AX, root, 5)
    assert records[0]['name'] in {'Draft note', 'Text Area'}
    assert records[0]['role'] == 'textbox'
    assert handles[0]['AXValue'] == 'Draft note'


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

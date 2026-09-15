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

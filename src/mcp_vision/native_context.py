"""Read-only native accessibility observation and guidance."""
from __future__ import annotations

from uuid import uuid4

from mcp_vision.browser import BrowserSnapshot
from mcp_vision.context import ContextBounds, ContextElement


def describe_ax(api, element):
    from mcp_vision.macos_ui import _ax_copy
    get = lambda key: _ax_copy(api, element, key)
    role = str(get('AXRole') or '')
    secure = 'secure' in role.lower() or str(get('AXSubrole') or '') == 'AXSecureTextField'
    bounds = None
    try:
        ok_p, p = api.AXValueGetValue(get('AXPosition'), api.kAXValueCGPointType, None)
        ok_s, s = api.AXValueGetValue(get('AXSize'), api.kAXValueCGSizeType, None)
        if ok_p and ok_s:
            bounds = ContextBounds(x=p.x, y=p.y, width=s.width, height=s.height)
    except Exception:
        pass
    value = get('AXValue') if not secure else ''
    return ContextElement(role=role, name=str(get('AXTitle') or get('AXDescription') or ''),
                          value=value if isinstance(value, str) else '', bounds=bounds)


def nearby_ax(api, root, limit=60):
    from mcp_vision.macos_ui import _ax_copy
    queue = [(root, 0)] if root else []
    records, handles = [], {}
    visited = 0
    while queue and visited < 180 and len(records) < limit:
        element, depth = queue.pop(0)
        visited += 1
        desc = describe_ax(api, element)
        box = desc.bounds
        if box and box.width > 0 and box.height > 0 and (desc.name or desc.value):
            index = len(records)
            records.append({'index': index, 'name': desc.name, 'role': desc.role, 'value': desc.value,
                            'x': box.x, 'y': box.y, 'w': box.width, 'h': box.height})
            handles[index] = element
        if depth < 5:
            queue.extend((child, depth + 1) for child in (_ax_copy(api, element, 'AXChildren') or [])[:60])
    return records, handles


class NativeContextBackend:
    def __init__(self, context, indicator=None):
        self.context = context
        self.indicator = indicator
        self.handles = {}
        self.sid = ''

    async def snapshot(self):
        import ApplicationServices as AX
        from mcp_vision.macos_ui import _ax_copy
        if not AX.AXIsProcessTrusted():
            raise PermissionError('Enable Accessibility for the process running MCP-Vision.')
        pid = self.context.accessibility_context.get('pid')
        if not pid:
            raise PermissionError('Invoke again on the native application to bind its accessibility target.')
        app = AX.AXUIElementCreateApplication(int(pid))
        window = _ax_copy(AX, app, 'AXFocusedWindow')
        title = str(_ax_copy(AX, window, 'AXTitle') or '')
        if self.context.title and title != self.context.title:
            raise PermissionError('The native window changed. Invoke again on the intended window.')
        records, self.handles = nearby_ax(AX, window)
        self.sid = uuid4().hex
        return BrowserSnapshot(snapshot_id=self.sid, url='', title=title,
                               text='\n'.join(e['name'] + ' ' + e['value'] for e in records),
                               elements=records, source='macos-accessibility')

    async def highlight(self, snapshot_id, index, label='Next step', duration=8000):
        if snapshot_id != self.sid or index not in self.handles or not self.indicator:
            return False
        import ApplicationServices as AX
        element = self.handles[index]
        original = describe_ax(AX, element)
        def resolve():
            fresh = describe_ax(AX, element)
            return fresh.bounds if fresh.name == original.name and fresh.role == original.role else None
        if not resolve():
            return False
        self.indicator(resolve, label, duration)
        return True

    async def clear_highlight(self):
        if self.indicator:
            self.indicator(None, '', 0)

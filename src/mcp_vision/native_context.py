"""Native macOS accessibility observation, guidance, and bounded Act."""
from __future__ import annotations

from uuid import uuid4

from mcp_vision.browser import BrowserSnapshot, Receipt
from mcp_vision.context import ContextBounds, ContextElement
from mcp_vision.redaction import redact


def role_label(role: str) -> str:
    import re
    text = (role or '').removeprefix('AX').strip()
    return re.sub(r'(?<!^)(?=[A-Z])', ' ', text).strip()


def normalize_role(role: str) -> str:
    mapping = {
        'AXButton': 'button', 'AXLink': 'link', 'AXTextField': 'textbox', 'AXTextArea': 'textbox',
        'AXCheckBox': 'checkbox', 'AXRadioButton': 'radio', 'AXPopUpButton': 'combobox',
        'AXComboBox': 'combobox', 'AXStaticText': 'text', 'AXMenuItem': 'menuitem',
        'AXTab': 'tab', 'AXSlider': 'slider', 'AXIncrementor': 'spinbutton',
    }
    return mapping.get(role, role.removeprefix('AX').lower() if role.startswith('AX') else role)


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
    checked = None
    if isinstance(value, bool):
        checked = value
        value = ''
    elif role in {'AXCheckBox', 'AXRadioButton'}:
        checked = value in (1, '1', True)
        value = ''
    return ContextElement(role=role, name=str(get('AXTitle') or get('AXDescription') or ''),
                          value=value if isinstance(value, str) else str(value or ''), bounds=bounds,
                          attributes={'checked': str(checked)} if checked is not None else {})


def nearby_ax(api, root, limit=60):
    from mcp_vision.macos_ui import _ax_copy
    queue = [(root, 0)] if root else []
    records, handles = [], {}
    visited = 0
    while queue and visited < 180 and len(records) < limit:
        element, depth = queue.pop(0)
        visited += 1
        if _ax_copy(api, element, 'AXHidden') is True:
            continue
        desc = describe_ax(api, element)
        box = desc.bounds
        if box and box.width > 0 and box.height > 0 and (desc.name or desc.value or desc.attributes.get('checked') is not None):
            index = len(records)
            name = desc.name.strip() or desc.value.strip()[:80] or role_label(desc.role)
            checked = desc.attributes.get('checked')
            records.append({
                'index': index, 'name': name, 'role': normalize_role(desc.role), 'ax_role': desc.role,
                'value': desc.value,
                'checked': None if checked is None else checked == 'True',
                'x': box.x, 'y': box.y, 'w': box.width, 'h': box.height,
            })
            handles[index] = element
        if depth < 5:
            queue.extend((child, depth + 1) for child in (_ax_copy(api, element, 'AXChildren') or [])[:60])
    return records, handles


class NativeContextBackend:
    def __init__(self, context, indicator=None, allow_writes=False):
        self.context = context
        self.indicator = indicator
        self.allow_writes = allow_writes
        self.handles = {}
        self.sid = ''

    def _pid(self) -> int:
        pid = self.context.accessibility_context.get('pid')
        if not pid:
            raise PermissionError('Invoke again on the native application to bind its accessibility target.')
        return int(pid)

    def _activate(self):
        from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(self._pid())
        if app:
            app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)

    def _element(self, snapshot_id, index):
        if snapshot_id != self.sid or index not in self.handles:
            raise LookupError('stale')
        return self.handles[index]

    def _blocked(self, action, message='Native writes are disabled.'):
        return Receipt(status='blocked', action=action, message=message, executed=False)

    async def snapshot(self):
        import ApplicationServices as AX
        from mcp_vision.macos_ui import _ax_copy
        if not AX.AXIsProcessTrusted():
            raise PermissionError('Enable Accessibility for /Applications/MCP-Vision.app.')
        app = AX.AXUIElementCreateApplication(self._pid())
        window = _ax_copy(AX, app, 'AXFocusedWindow') or _ax_copy(AX, app, 'AXMainWindow')
        title = str(_ax_copy(AX, window, 'AXTitle') or '')
        # Act may change panes/titles inside the same app; keep the pid bound.
        if self.context.title and title and title != self.context.title and not self.allow_writes:
            raise PermissionError('The native window changed. Invoke again on the intended window.')
        if title:
            self.context = self.context.model_copy(update={'title': title})
        records, self.handles = nearby_ax(AX, window or app)
        self.sid = uuid4().hex
        return BrowserSnapshot(snapshot_id=self.sid, url='', title=title,
                               text='\n'.join(e['name'] + ' ' + str(e.get('value') or '') for e in records),
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

    async def click(self, snapshot_id, index):
        if not self.allow_writes:
            return self._blocked('click')
        try:
            element = self._element(snapshot_id, index)
        except LookupError:
            return Receipt(status='stale', action='click', message='Target is no longer available.', executed=False)
        try:
            import ApplicationServices as AX
            from mcp_vision.macos_ui import _ax_copy
            self._activate()
            actions = [str(a) for a in (_ax_copy(AX, element, 'AXActions') or [])]
            if 'AXPress' in actions or not actions:
                err = AX.AXUIElementPerformAction(element, 'AXPress')
                if err == 0:
                    return Receipt(status='unverified', action='click', executed=True,
                                   message='Clicked via Accessibility. Re-observe before claiming success.')
            desc = describe_ax(AX, element)
            box = desc.bounds
            if not box:
                return Receipt(status='error', action='click', message='No bounds for native click.', executed=False)
            from mcp_vision.core.actuate import get_actuator
            get_actuator().click(int(box.x + box.width / 2), int(box.y + box.height / 2))
            return Receipt(status='unverified', action='click', executed=True,
                           message='Clicked via pointer. Re-observe before claiming success.')
        except Exception as exc:
            return Receipt(status='error', action='click', message=redact(str(exc)), executed=None)

    async def fill(self, snapshot_id, index, text):
        if not self.allow_writes:
            return self._blocked('fill')
        try:
            element = self._element(snapshot_id, index)
        except LookupError:
            return Receipt(status='stale', action='fill', message='Target is no longer available.', executed=False)
        try:
            import ApplicationServices as AX
            self._activate()
            AX.AXUIElementSetAttributeValue(element, 'AXFocused', True)
            err = AX.AXUIElementSetAttributeValue(element, 'AXValue', str(text))
            if err != 0:
                from mcp_vision.core.actuate import get_actuator
                get_actuator().type_text(str(text), press_enter=False)
            fresh = describe_ax(AX, element)
            matches = (fresh.value or '') == str(text)
            return Receipt(status='verified' if matches else 'unverified', action='fill', executed=True,
                           message='Filled native field.' if matches else 'Fill dispatched; value did not match yet.',
                           evidence={'value': fresh.value})
        except Exception as exc:
            return Receipt(status='error', action='fill', message=redact(str(exc)), executed=None)

    async def select(self, snapshot_id, index, value):
        # Native selects are inconsistent; click then rely on planner to continue.
        receipt = await self.click(snapshot_id, index)
        if receipt.status in {'blocked', 'stale', 'error'}:
            return receipt.model_copy(update={'action': 'select'})
        return Receipt(status='unverified', action='select', executed=True,
                       message=f'Opened native control to choose {value!r}. Continue with the visible option.')

    async def set_checked(self, snapshot_id, index, checked: bool):
        if not self.allow_writes:
            return self._blocked('set_checked')
        try:
            element = self._element(snapshot_id, index)
        except LookupError:
            return Receipt(status='stale', action='set_checked', message='Target is no longer available.', executed=False)
        try:
            import ApplicationServices as AX
            self._activate()
            current = describe_ax(AX, element).attributes.get('checked') == 'True'
            if current is bool(checked):
                return Receipt(status='verified', action='set_checked', executed=True,
                               message='Checkbox already in the requested state.', evidence={'checked': current})
            err = AX.AXUIElementPerformAction(element, 'AXPress')
            if err != 0:
                return await self.click(snapshot_id, index)
            after = describe_ax(AX, element).attributes.get('checked') == 'True'
            return Receipt(status='verified' if after is bool(checked) else 'unverified', action='set_checked',
                           executed=True, message='Updated native checkbox.', evidence={'checked': after})
        except Exception as exc:
            return Receipt(status='error', action='set_checked', message=redact(str(exc)), executed=None)

    async def upload(self, snapshot_id, index, path):
        return self._blocked('upload', 'Native file upload is not supported in this backend.')

    async def scroll(self, snapshot_id, delta_y):
        if snapshot_id != self.sid:
            return Receipt(status='stale', action='scroll', message='Snapshot expired.', executed=False)
        try:
            from mcp_vision.core.actuate import get_actuator
            self._activate()
            # Bounded wheel-style scroll via page keys when delta is large.
            if abs(delta_y) >= 400:
                get_actuator().press(['pagedown' if delta_y > 0 else 'pageup'])
            else:
                get_actuator().press(['down' if delta_y > 0 else 'up'])
            return Receipt(status='unverified', action='scroll', executed=True, message='Scrolled native view.')
        except Exception as exc:
            return Receipt(status='error', action='scroll', message=redact(str(exc)), executed=None)

    async def verify_text(self, text):
        snap = await self.snapshot()
        found = text in snap.text
        return Receipt(status='verified' if found else 'unverified', action='verify_text', executed=False,
                       message='Text present.' if found else 'Text not observed.', evidence={'text': text})

    async def tabs(self):
        return {'tabs': []}

    async def use_tab(self, tab_id, expected_url):
        return Receipt(status='error', action='use_tab', message='Native apps do not use browser tabs.')

    async def open_tab(self, url):
        return Receipt(status='error', action='open_tab', message='Native apps do not use browser tabs.')

    async def navigate(self, url):
        return Receipt(status='error', action='navigate', message='Native apps do not navigate URLs.')

    async def act(self, action, name, role='', text=''):
        snap = await self.snapshot()
        matches = [e for e in snap.elements if e['name'] == name and (not role or e['role'] == role)]
        if len(matches) != 1:
            return Receipt(status='blocked', action=action, message='Ambiguous or missing native target.')
        index = matches[0]['index']
        if action == 'click':
            return await self.click(snap.snapshot_id, index)
        if action == 'fill':
            return await self.fill(snap.snapshot_id, index, text)
        return Receipt(status='blocked', action=action, message='Unsupported native action.')

    async def screenshot(self):
        return b''

    async def close(self):
        await self.clear_highlight()

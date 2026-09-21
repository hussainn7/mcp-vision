"""Native macOS accessibility observation, guidance, and bounded Act."""
from __future__ import annotations

from uuid import uuid4

from mcp_vision.browser import BrowserSnapshot, Receipt
from mcp_vision.context import ContextBounds, ContextElement
from mcp_vision.execution_ladder import AttemptOutcome, ExecutionMethod, ExecutionTrace
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
    queue = [(root, 0, "root")] if root else []
    records, handles = [], {}
    visited = 0
    while queue and visited < 180 and len(records) < limit:
        element, depth, path = queue.pop(0)
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
                'identity': {'accessibility': path}, 'ax_ref': path,
            })
            handles[index] = element
        if depth < 5:
            queue.extend((child, depth + 1, f"{path}/{offset}")
                         for offset, child in enumerate((_ax_copy(api, element, 'AXChildren') or [])[:60]))
    return records, handles


class NativeContextBackend:
    def __init__(self, context, indicator=None, allow_writes=False):
        self.context = context
        self.indicator = indicator
        self.allow_writes = allow_writes
        self.handles = {}
        self.records = {}
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
            return True
        return False

    def _element(self, snapshot_id, index):
        if snapshot_id != self.sid or index not in self.handles:
            raise LookupError('stale')
        return self.handles[index]

    def _fresh_element(self, snapshot_id, index, api):
        element = self._element(snapshot_id, index)
        original = self.records.get(index)
        fresh = describe_ax(api, element)
        if not original or fresh.role != original.get('ax_role') or fresh.name != original.get('name'):
            raise LookupError('stale')
        box = fresh.bounds
        if not box or any(round(value) != round(original[key]) for value, key in (
            (box.x, 'x'), (box.y, 'y'), (box.width, 'w'), (box.height, 'h'),
        )):
            raise LookupError('stale')
        if fresh.value != str(original.get('value') or ''):
            raise LookupError('stale')
        checked = fresh.attributes.get('checked')
        if checked is not None and (checked == 'True') is not original.get('checked'):
            raise LookupError('stale')
        return element

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
        self.records = {record['index']: record for record in records}
        self.sid = uuid4().hex
        return BrowserSnapshot(snapshot_id=self.sid, root_id=f"macos-pid-{self._pid()}", url='', title=title,
                               text='\n'.join(e['name'] + ' ' + str(e.get('value') or '') for e in records),
                               elements=records, source='macos-accessibility', identity={
                                   'pid': self._pid(),
                                   'application': self.context.source_application,
                                   'window_title': title,
                               })

    async def find(self, *, role='', name='', value=''):
        """Return semantic native matches from one fresh window-scoped observation."""
        snapshot = await self.snapshot()
        matches = [record for record in snapshot.elements
                   if (not role or record.get('role') == role)
                   and (not name or name.casefold() in str(record.get('name') or '').casefold())
                   and (not value or value.casefold() in str(record.get('value') or '').casefold())]
        return {'state_id': snapshot.snapshot_id, 'root_id': snapshot.root_id, 'matches': matches}

    async def wait_for(self, *, role='', name='', value='', gone=False, timeout_ms=3000):
        """Wait for an AX predicate by re-resolving the bound window; no input is delivered."""
        import asyncio
        import time
        if not 0 <= timeout_ms <= 5000:
            raise ValueError('timeout_ms must be between 0 and 5000')
        deadline = time.monotonic() + timeout_ms / 1000
        last = None
        while True:
            last = await self.find(role=role, name=name, value=value)
            satisfied = not last['matches'] if gone else bool(last['matches'])
            if satisfied or time.monotonic() >= deadline:
                return {**last, 'satisfied': satisfied, 'found': bool(last['matches']),
                        'gone': gone, 'timed_out': not satisfied}
            await asyncio.sleep(0.05)

    async def settle(self, operation=''):
        import asyncio
        await asyncio.sleep(0.05)

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
            import ApplicationServices as AX
            element = self._fresh_element(snapshot_id, index, AX)
        except LookupError:
            return Receipt(status='stale', action='click', message='Target is no longer available.', executed=False)
        try:
            from mcp_vision.macos_ui import _ax_copy
            trace = ExecutionTrace()
            actions = [str(a) for a in (_ax_copy(AX, element, 'AXActions') or [])]
            if 'AXPress' in actions or not actions:
                err = AX.AXUIElementPerformAction(element, 'AXPress')
                if err == 0:
                    trace.add(ExecutionMethod.AX_BACKGROUND, AttemptOutcome.UNKNOWN, background=True,
                              detail='AXPress dispatched without activating the application')
                    return Receipt(status='unverified', action='click', executed=True,
                                   message='Pressed via background Accessibility; verify the successor state.',
                                   evidence=trace.evidence())
                trace.add(ExecutionMethod.AX_BACKGROUND, AttemptOutcome.DIDNT, background=True,
                          detail=f'AXPress returned {err}')
            self._activate()
            if 'AXPress' in actions or not actions:
                err = AX.AXUIElementPerformAction(element, 'AXPress')
                if err == 0:
                    trace.add(ExecutionMethod.AX_FOREGROUND, AttemptOutcome.UNKNOWN, background=False,
                              detail='AXPress dispatched after foreground activation')
                    return Receipt(status='unverified', action='click', executed=True,
                                   message='Pressed via foreground Accessibility; verify the successor state.',
                                   evidence=trace.evidence())
                trace.add(ExecutionMethod.AX_FOREGROUND, AttemptOutcome.DIDNT, background=False,
                          detail=f'AXPress returned {err}')
            desc = describe_ax(AX, element)
            box = desc.bounds
            if not box:
                return Receipt(status='error', action='click', message='No bounds for native click.', executed=False,
                               evidence=trace.evidence())
            from mcp_vision.core.actuate import get_actuator
            get_actuator().click(int(box.x + box.width / 2), int(box.y + box.height / 2))
            trace.add(ExecutionMethod.FOREGROUND_POINTER, AttemptOutcome.UNKNOWN, background=False,
                      detail='Pointer fallback dispatched at fresh AX bounds')
            return Receipt(status='unverified', action='click', executed=True,
                           message='Clicked via foreground pointer fallback; verify the successor state.',
                           evidence=trace.evidence())
        except Exception as exc:
            return Receipt(status='error', action='click', message=redact(str(exc)), executed=None)

    async def fill(self, snapshot_id, index, text):
        if not self.allow_writes:
            return self._blocked('fill')
        try:
            import ApplicationServices as AX
            element = self._fresh_element(snapshot_id, index, AX)
        except LookupError:
            return Receipt(status='stale', action='fill', message='Target is no longer available.', executed=False)
        try:
            trace = ExecutionTrace()
            AX.AXUIElementSetAttributeValue(element, 'AXFocused', True)
            err = AX.AXUIElementSetAttributeValue(element, 'AXValue', str(text))
            if err == 0:
                fresh = describe_ax(AX, element)
                matches = (fresh.value or '') == str(text)
                trace.add(ExecutionMethod.AX_BACKGROUND,
                          AttemptOutcome.WORKED if matches else AttemptOutcome.UNKNOWN,
                          background=True, detail='AXValue write', evidence={'value_matches': matches})
                return Receipt(status='verified' if matches else 'unverified', action='fill', executed=True,
                               message='Filled via background Accessibility.' if matches
                               else 'Background AXValue dispatched; read-back did not match.',
                               evidence={**trace.evidence(), 'value': fresh.value})
            trace.add(ExecutionMethod.AX_BACKGROUND, AttemptOutcome.DIDNT, background=True,
                      detail=f'AXValue returned {err}')
            try:
                from mcp_vision.macos_input import targeted_replace_text
                targeted_replace_text(self._pid(), str(text))
                fresh = describe_ax(AX, element)
                matches = (fresh.value or '') == str(text)
                trace.add(ExecutionMethod.PID_KEYBOARD,
                          AttemptOutcome.WORKED if matches else AttemptOutcome.UNKNOWN,
                          background=True, detail='PID-targeted replace text', evidence={'value_matches': matches})
                return Receipt(status='verified' if matches else 'unverified', action='fill', executed=True,
                               message='Filled via PID-targeted keyboard.' if matches
                               else 'PID-targeted input dispatched; read-back did not match.',
                               evidence={**trace.evidence(), 'value': fresh.value})
            except Exception as targeted_error:
                trace.add(ExecutionMethod.PID_KEYBOARD, AttemptOutcome.DIDNT, background=True,
                          detail=type(targeted_error).__name__)
            self._activate()
            err = AX.AXUIElementSetAttributeValue(element, 'AXValue', str(text))
            if err == 0:
                fresh = describe_ax(AX, element)
                matches = (fresh.value or '') == str(text)
                trace.add(ExecutionMethod.AX_FOREGROUND,
                          AttemptOutcome.WORKED if matches else AttemptOutcome.UNKNOWN,
                          background=False, detail='AXValue after foreground activation')
                return Receipt(status='verified' if matches else 'unverified', action='fill', executed=True,
                               message='Filled via foreground Accessibility.' if matches
                               else 'Foreground AXValue dispatched; read-back did not match.',
                               evidence={**trace.evidence(), 'value': fresh.value})
            trace.add(ExecutionMethod.AX_FOREGROUND, AttemptOutcome.DIDNT, background=False,
                      detail=f'AXValue returned {err}')
            from mcp_vision.core.actuate import get_actuator
            get_actuator().type_text(str(text), press_enter=False)
            trace.add(ExecutionMethod.FOREGROUND_KEYBOARD, AttemptOutcome.UNKNOWN, background=False,
                      detail='Global keyboard fallback dispatched')
            return Receipt(status='unverified', action='fill', executed=True,
                           message='Foreground keyboard input dispatched; re-observe before retrying.',
                           evidence=trace.evidence())
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
            import ApplicationServices as AX
            element = self._fresh_element(snapshot_id, index, AX)
        except LookupError:
            return Receipt(status='stale', action='set_checked', message='Target is no longer available.', executed=False)
        try:
            trace = ExecutionTrace()
            current = describe_ax(AX, element).attributes.get('checked') == 'True'
            if current is bool(checked):
                return Receipt(status='verified', action='set_checked', executed=True,
                               message='Checkbox already in the requested state.',
                               evidence={'checked': current, 'execution_path': 'none', 'background': True})
            err = AX.AXUIElementPerformAction(element, 'AXPress')
            if err != 0:
                trace.add(ExecutionMethod.AX_BACKGROUND, AttemptOutcome.DIDNT, background=True,
                          detail=f'AXPress returned {err}')
                self._activate()
                err = AX.AXUIElementPerformAction(element, 'AXPress')
                method, background = ExecutionMethod.AX_FOREGROUND, False
            else:
                method, background = ExecutionMethod.AX_BACKGROUND, True
            after = describe_ax(AX, element).attributes.get('checked') == 'True'
            outcome = (AttemptOutcome.DIDNT if err != 0 else
                       AttemptOutcome.WORKED if after is bool(checked) else AttemptOutcome.UNKNOWN)
            trace.add(method, outcome,
                      background=background, evidence={'checked': after})
            return Receipt(status='verified' if after is bool(checked) else 'unverified', action='set_checked',
                           executed=err == 0, message='Updated native checkbox.' if err == 0 else 'Checkbox action failed.',
                           evidence={**trace.evidence(), 'checked': after})
        except Exception as exc:
            return Receipt(status='error', action='set_checked', message=redact(str(exc)), executed=None)

    async def upload(self, snapshot_id, index, path):
        return self._blocked('upload', 'Native file upload is not supported in this backend.')

    async def scroll(self, snapshot_id, delta_y):
        if snapshot_id != self.sid:
            return Receipt(status='stale', action='scroll', message='Snapshot expired.', executed=False)
        try:
            trace = ExecutionTrace()
            keycode = (121 if delta_y > 0 else 116) if abs(delta_y) >= 400 else (125 if delta_y > 0 else 126)
            try:
                from mcp_vision.macos_input import targeted_key
                targeted_key(self._pid(), keycode)
                trace.add(ExecutionMethod.PID_KEYBOARD, AttemptOutcome.UNKNOWN, background=True,
                          detail='PID-targeted scroll key dispatched')
                return Receipt(status='unverified', action='scroll', executed=True,
                               message='Scrolled with PID-targeted keyboard; verify the successor state.',
                               evidence=trace.evidence())
            except Exception as targeted_error:
                trace.add(ExecutionMethod.PID_KEYBOARD, AttemptOutcome.DIDNT, background=True,
                          detail=type(targeted_error).__name__)
            from mcp_vision.core.actuate import get_actuator
            self._activate()
            key = ('pagedown' if delta_y > 0 else 'pageup') if abs(delta_y) >= 400 else ('down' if delta_y > 0 else 'up')
            get_actuator().press([key])
            trace.add(ExecutionMethod.FOREGROUND_KEYBOARD, AttemptOutcome.UNKNOWN, background=False,
                      detail='Global scroll key dispatched')
            return Receipt(status='unverified', action='scroll', executed=True,
                           message='Scrolled with foreground keyboard fallback; verify the successor state.',
                           evidence=trace.evidence())
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

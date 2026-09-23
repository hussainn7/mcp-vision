"""Native macOS accessibility observation, guidance, and bounded Act."""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from uuid import uuid4

from mcp_vision.browser import BrowserSnapshot, Receipt
from mcp_vision.context import ContextBounds, ContextElement
from mcp_vision.core.governor import Governor
from mcp_vision.core.models import Policy
from mcp_vision.execution_ladder import (
    AttemptOutcome, ExecutionMethod, ExecutionTrace, RefusalReason,
)
from mcp_vision.native_perception import (
    WindowCapture, capture_window, degradation_reasons, fuse_ax_ocr, recognize_text,
    same_window_bounds, visible_window_ids,
)
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
        'AXMenuButton': 'button', 'AXDisclosureTriangle': 'button', 'AXSearchField': 'textbox',
        'AXRow': 'row', 'AXCell': 'cell',
    }
    return mapping.get(role, role.removeprefix('AX').lower() if role.startswith('AX') else role)


def describe_ax(api, element):
    from mcp_vision.macos_ui import _ax_copy
    get = lambda key: _ax_copy(api, element, key)
    role = str(get('AXRole') or '')
    subrole = str(get('AXSubrole') or '')
    secure = 'secure' in role.lower() or subrole == 'AXSecureTextField'
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
    name = str(get('AXTitle') or get('AXDescription') or get('AXHelp') or '')
    if not name and subrole and subrole != 'AXSecureTextField':
        name = role_label(subrole)
    return ContextElement(role=role, name=name,
                          value=value if isinstance(value, str) else str(value or ''), bounds=bounds,
                          attributes={'checked': str(checked)} if checked is not None else {})


def nearby_ax(api, root, limit=120, *, node_cap=4000, time_cap=.6,
              include_offscreen_pressable=False):
    """Walk a native window deeply enough to reach browser/app toolbars.

    Chromium and Electron commonly put visible controls hundreds of nodes below
    the window root.  The old depth-5/180-node walk silently omitted controls
    such as Chrome's New Tab button.  Bound the work by nodes and wall time,
    not an arbitrary tree depth, while keeping the planner payload capped.
    """
    from mcp_vision.macos_ui import _ax_copy
    queue = deque([(root, "root", "", False)]) if root else deque()
    candidates, deferred = [], []
    visited = 0
    seen = set()
    deadline = time.monotonic() + max(0, time_cap)
    semantic_roles = {
        'AXButton', 'AXTextField', 'AXTextArea', 'AXSearchField', 'AXCheckBox', 'AXRadioButton',
        'AXPopUpButton', 'AXComboBox', 'AXSlider', 'AXIncrementor', 'AXMenuButton',
        'AXDisclosureTriangle', 'AXLink', 'AXTab', 'AXRow', 'AXCell',
    }
    while queue and visited < node_cap and time.monotonic() <= deadline:
        element, path, inherited_label, in_web_content = queue.popleft()
        identity = id(element)
        if identity in seen:
            continue
        seen.add(identity)
        visited += 1
        if _ax_copy(api, element, 'AXHidden') is True and not include_offscreen_pressable:
            continue
        desc = describe_ax(api, element)
        box = desc.bounds
        semantic_control = desc.role in semantic_roles
        children = list((_ax_copy(api, element, 'AXChildren') or [])[:node_cap])
        descendant_label = ''
        if not desc.name and desc.role in {'AXRow', 'AXCell'}:
            for child in children[:8]:
                child_desc = describe_ax(api, child)
                if child_desc.role == 'AXStaticText' and (child_desc.name or child_desc.value):
                    descendant_label = child_desc.name or child_desc.value[:80]
                    break
        semantic_name = desc.name.strip() or descendant_label or inherited_label
        actions = [str(action) for action in (_ax_copy(api, element, 'AXActions') or [])]
        visible = bool(box and box.width > 0 and box.height > 0)
        offscreen_pressable = bool(include_offscreen_pressable and semantic_name
                                   and desc.role == 'AXMenuItem' and 'AXPress' in actions)
        if (visible or offscreen_pressable) and (
                semantic_name or desc.value or desc.attributes.get('checked') is not None or semantic_control):
            # Editable controls need a stable semantic name: their value changes
            # after a successful fill and must not become their identity.
            name = (semantic_name or role_label(desc.role)) if semantic_control else (
                semantic_name or desc.value.strip()[:80] or role_label(desc.role))
            checked = desc.attributes.get('checked')
            record = {
                'index': -1, 'name': name, 'role': normalize_role(desc.role), 'ax_role': desc.role,
                'ax_name': desc.name,
                'sources': ['ax'],
                'value': desc.value,
                'checked': None if checked is None else checked == 'True',
                'x': box.x if visible else 0, 'y': box.y if visible else 0,
                'w': box.width if visible else 0, 'h': box.height if visible else 0,
                'offscreen': offscreen_pressable,
                'identity': {'accessibility': path}, 'ax_ref': path,
            }
            if in_web_content:
                deferred.append((record, element))
            else:
                candidates.append((record, element))
        child_label = semantic_name if desc.role in {'AXButton', 'AXLink', 'AXTab', 'AXRow', 'AXCell'} else ''
        child_in_web = in_web_content or desc.role in {'AXWebArea', 'AXDocument'}
        queue.extend((child, f"{path}/{offset}", child_label, child_in_web)
                     for offset, child in enumerate(children))
    elapsed = time.monotonic() - (deadline - max(0, time_cap))
    if len(candidates) + len(deferred) < 8 or elapsed > time_cap * 0.9:
        import sys
        print(f"[nearby_ax] records={len(candidates)}+{len(deferred)} visited={visited} "
              f"elapsed={elapsed:.2f}s cap={time_cap}s", file=sys.stderr, flush=True)
    candidates.extend(deferred)
    priority = {
        'textbox': 0, 'combobox': 0, 'checkbox': 0, 'radio': 0, 'slider': 0,
        'spinbutton': 0, 'button': 1, 'link': 1, 'tab': 1, 'menuitem': 1,
        'row': 3, 'cell': 3,
    }
    candidates.sort(key=lambda item: priority.get(item[0]['role'], 2))
    records, handles = [], {}
    for record, element in candidates[:limit]:
        index = len(records)
        records.append({**record, 'index': index})
        handles[index] = element
    return records, handles


@dataclass(frozen=True)
class NativePreflight:
    capture: WindowCapture
    observed_capture: WindowCapture
    window: object
    element: object | None
    record: dict
    sibling_window_ids: tuple[int, ...]
    keyboard_unambiguous: bool
    authority: dict


class NativePreflightError(RuntimeError):
    def __init__(self, reason: RefusalReason, message: str, *, evidence=None):
        super().__init__(message)
        self.reason = reason
        self.evidence = evidence or {}


class NativeContextBackend:
    def __init__(self, context, indicator=None, allow_writes=False, governor=None):
        self.context = context
        self.indicator = indicator
        self.allow_writes = allow_writes
        self.governor = governor or Governor()
        self.handles = {}
        self.records = {}
        self.sid = ''
        self._captures = {}
        self._windows = {}

    def _pid(self) -> int:
        pid = self.context.accessibility_context.get('pid')
        if not pid:
            raise PermissionError('Invoke again on the native application to bind its accessibility target.')
        return int(pid)

    def _activate(self):
        # AppKit calls belong on the main thread; the task loop runs on a
        # worker thread inside the packaged app, so marshal the activation.
        import threading
        from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication

        def activate():
            try:
                app = NSRunningApplication.runningApplicationWithProcessIdentifier_(self._pid())
                if app:
                    app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            except Exception:
                pass

        if threading.current_thread() is threading.main_thread():
            activate()
            return True
        try:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(activate)
            return True
        except Exception:
            activate()
            return True

    def _element(self, snapshot_id, index):
        if snapshot_id != self.sid or index not in self.handles:
            raise LookupError('stale')
        return self.handles[index]

    def _fresh_element(self, snapshot_id, index, api):
        element = self._element(snapshot_id, index)
        original = self.records.get(index)
        fresh = describe_ax(api, element)
        if (not original or fresh.role != original.get('ax_role')
                or fresh.name != original.get('ax_name', original.get('name'))):
            raise LookupError('stale')
        box = fresh.bounds
        if not original.get('offscreen'):
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

    @staticmethod
    def _bounds_dict(bounds):
        return {'X': bounds.x, 'Y': bounds.y, 'Width': bounds.width, 'Height': bounds.height}

    async def _preflight(self, snapshot_id, index, api, *, require_element=True) -> NativePreflight:
        import asyncio
        from mcp_vision.macos_ui import _ax_copy

        if snapshot_id != self.sid:
            raise NativePreflightError(RefusalReason.STALE_CAPTURE, 'Observation authority has expired.')
        observed = self._captures.get(snapshot_id)
        window = self._windows.get(snapshot_id)
        record = self.records.get(index)
        if not observed or not window or not record:
            raise NativePreflightError(
                RefusalReason.WINDOW_AUTHORITY_MISSING,
                'The observation has no exact target-window authority.')
        app = api.AXUIElementCreateApplication(self._pid())
        if _ax_copy(api, app, 'AXHidden') is True or _ax_copy(api, window, 'AXHidden') is True:
            raise NativePreflightError(RefusalReason.WINDOW_HIDDEN, 'The target window is hidden.')
        if _ax_copy(api, window, 'AXMinimized') is True:
            raise NativePreflightError(RefusalReason.WINDOW_MINIMIZED, 'The target window is minimized.')
        windows = _ax_copy(api, app, 'AXWindows') or []
        if windows and not any(candidate == window for candidate in windows):
            raise NativePreflightError(RefusalReason.WINDOW_NOT_FOUND,
                                       'The observed Accessibility window is no longer available.')
        current_bounds = describe_ax(api, window).bounds
        if not current_bounds or not same_window_bounds(observed.bounds, self._bounds_dict(current_bounds)):
            raise NativePreflightError(RefusalReason.WINDOW_MOVED, 'The target window moved after observation.')
        ids = await asyncio.to_thread(visible_window_ids, self._pid())
        if observed.window_id not in ids:
            raise NativePreflightError(RefusalReason.WINDOW_NOT_FOUND, 'The target WindowServer window is unavailable.')
        try:
            fresh_capture = await asyncio.to_thread(
                capture_window, self._pid(), observed.bounds, self.context.title, observed.window_id)
        except Exception as exc:
            raise NativePreflightError(
                RefusalReason.STALE_CAPTURE, 'The exact target-window capture could not be refreshed.',
                evidence={'error': type(exc).__name__}) from exc
        if (fresh_capture.pid != observed.pid
                or fresh_capture.window_id != observed.window_id
                or not same_window_bounds(fresh_capture.bounds, observed.bounds)
                or abs(float(fresh_capture.scale) - float(observed.scale)) > 0.02):
            raise NativePreflightError(
                RefusalReason.STALE_CAPTURE,
                'The refreshed capture does not match the observed target-window authority.',
                evidence={
                    'observed_window_id': observed.window_id,
                    'fresh_window_id': fresh_capture.window_id,
                    'observed_pid': observed.pid,
                    'fresh_pid': fresh_capture.pid,
                })
        element = None
        if require_element:
            try:
                element = self._fresh_element(snapshot_id, index, api)
            except LookupError as exc:
                raise NativePreflightError(RefusalReason.TARGET_CHANGED, 'The native target changed.') from exc
        focused_window = _ax_copy(api, app, 'AXFocusedWindow') or _ax_copy(api, app, 'AXMainWindow')
        focused_bounds = describe_ax(api, focused_window).bounds if focused_window else None
        keyboard_unambiguous = (
            ids == (observed.window_id,)
            and focused_bounds is not None
            and same_window_bounds(observed.bounds, self._bounds_dict(focused_bounds))
        )
        authority = {
            'pid': observed.pid, 'window_id': observed.window_id,
            'bounds': observed.bounds, 'scale': observed.scale,
            'visible_window_ids': list(ids),
        }
        return NativePreflight(
            capture=fresh_capture, observed_capture=observed, window=window,
            element=element, record=record, sibling_window_ids=ids,
            keyboard_unambiguous=keyboard_unambiguous, authority=authority,
        )

    def _refused(self, action, trace, error: NativePreflightError):
        trace.refuse(error.reason)
        evidence = {**trace.evidence(), **error.evidence}
        stale = error.reason in {
            RefusalReason.STALE_CAPTURE, RefusalReason.WINDOW_MOVED,
            RefusalReason.TARGET_CHANGED, RefusalReason.PIXELS_CHANGED,
        }
        return Receipt(status='stale' if stale else 'blocked', action=action,
                       message=str(error), executed=False, evidence=evidence)

    def _authorize_foreground(self, action, record, trace) -> bool:
        label = str(record.get('name') or record.get('role') or 'native target')
        summary = f'Bring {self.context.source_application or self.context.title or "the target app"} forward to {action} {label}'
        allowed = self.governor.allow(Policy.RESTRICTED_ACTION, summary)
        trace.focus.update({'approval_required': True, 'approval_granted': allowed})
        if not allowed:
            trace.refuse(RefusalReason.FOREGROUND_NOT_AUTHORIZED)
        return allowed

    async def _focus_exact_window(self, api, preflight: NativePreflight, trace) -> bool:
        import asyncio
        from mcp_vision.macos_ui import _ax_copy
        from mcp_vision.native_apps import _frontmost

        before_pid = _frontmost()[2]
        try:
            api.AXUIElementSetAttributeValue(preflight.window, 'AXMain', True)
            api.AXUIElementSetAttributeValue(preflight.window, 'AXFocused', True)
        except Exception:
            pass
        self._activate()
        after_pid = _frontmost()[2]
        for _ in range(10):
            if after_pid == self._pid():
                break
            await asyncio.sleep(0.05)
            after_pid = _frontmost()[2]
        app = api.AXUIElementCreateApplication(self._pid())
        focused = _ax_copy(api, app, 'AXFocusedWindow') or _ax_copy(api, app, 'AXMainWindow')
        bounds = describe_ax(api, focused).bounds if focused else None
        exact = bool(bounds and same_window_bounds(preflight.capture.bounds, self._bounds_dict(bounds)))
        trace.focus.update({
            'before_pid': before_pid, 'after_pid': after_pid,
            'changed': before_pid != after_pid, 'exact_window_focused': exact,
            'requested': True,
            'behavior': 'changed' if before_pid != after_pid else 'already_frontmost',
        })
        if after_pid != self._pid():
            trace.refuse(RefusalReason.FRONTMOST_MISMATCH)
            return False
        if not exact:
            trace.refuse(RefusalReason.ACTIVATION_FAILED)
            return False
        return True

    def _blocked(self, action, message='Native writes are disabled.'):
        return Receipt(status='blocked', action=action, message=message, executed=False)

    async def snapshot(self):
        import asyncio
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
        window_bounds = describe_ax(AX, window).bounds if window else None
        capture_task = None
        if window_bounds:
            capture_task = asyncio.create_task(asyncio.to_thread(
                capture_window, self._pid(),
                {'X': window_bounds.x, 'Y': window_bounds.y,
                 'Width': window_bounds.width, 'Height': window_bounds.height},
                title,
            ))
        traversal_error = False
        try:
            records, self.handles = await asyncio.to_thread(nearby_ax, AX, window or app)
        except Exception:
            records, self.handles = [], {}
            traversal_error = True
        if len(records) < 8:
            # An app whose AX server just woke up can answer with a truncated
            # tree. Give it one short beat and walk again before concluding
            # the surface is truly this sparse.
            await asyncio.sleep(0.3)
            app = AX.AXUIElementCreateApplication(self._pid())
            window = _ax_copy(AX, app, 'AXFocusedWindow') or _ax_copy(AX, app, 'AXMainWindow')
            title = str(_ax_copy(AX, window, 'AXTitle') or '') or title
            try:
                records2, handles2 = await asyncio.to_thread(nearby_ax, AX, window or app)
            except Exception:
                records2, handles2 = [], {}
                traversal_error = True
            if len(records2) > len(records):
                records, self.handles = records2, handles2
        # Some native editors (e.g. Notes blank note) are not reached by the
        # window BFS but are the AXFocusedUIElement. Include the focused
        # element if it is an editable control and not already in the snapshot.
        focused = _ax_copy(AX, app, 'AXFocusedUIElement')
        if focused:
            focused_desc = describe_ax(AX, focused)
            already = any(
                str(r.get('ax_role', '')) == focused_desc.role
                and str(r.get('ax_name', '')) == focused_desc.name
                and r.get('role') in {'textbox', 'combobox'}
                for r in records
            )
            if not already and focused_desc.role in {
                'AXTextField', 'AXTextArea', 'AXSearchField', 'AXComboBox',
            }:
                box = focused_desc.bounds
                visible = bool(box and box.width > 0 and box.height > 0)
                if visible:
                    index = len(records)
                    name = (focused_desc.name.strip() or focused_desc.value.strip()[:80]
                            or role_label(focused_desc.role))
                    record = {
                        'index': index, 'name': name, 'role': normalize_role(focused_desc.role),
                        'ax_role': focused_desc.role, 'ax_name': focused_desc.name,
                        'sources': ['ax'],
                        'value': focused_desc.value,
                        'checked': None,
                        'x': box.x, 'y': box.y, 'w': box.width, 'h': box.height,
                        'offscreen': False, 'identity': {'accessibility': 'focused'},
                        'ax_ref': 'focused',
                    }
                    records.append(record)
                    self.handles[index] = focused
        # Closed app menus expose semantic commands through AXPress even when
        # they have no on-screen bounds. Add labelled commands that are absent
        # from the visible window. This discovers capabilities generically;
        # there is no command phrase or shortcut table in the planning path.
        menu = _ax_copy(AX, app, 'AXMenuBar')
        if menu and len(records) < 120:
            menu_records, menu_handles = nearby_ax(
                AX, menu, 120 - len(records), node_cap=1200, time_cap=.2,
                include_offscreen_pressable=True)
            observed_names = {str(record.get('name') or '').casefold() for record in records}
            for menu_record in menu_records:
                name = str(menu_record.get('name') or '').casefold()
                if not name or name in observed_names or not menu_record.get('offscreen'):
                    continue
                old_index = menu_record['index']
                index = len(records)
                path = f"menu/{menu_record.get('ax_ref', old_index)}"
                records.append({**menu_record, 'index': index, 'ax_ref': path,
                                'identity': {'accessibility': path}})
                self.handles[index] = menu_handles[old_index]
                observed_names.add(name)
        capture = None
        capture_error = ''
        if capture_task:
            try:
                capture = await capture_task
            except Exception as exc:
                capture_error = type(exc).__name__
        else:
            capture_error = 'MissingAXWindowBounds'

        final_bounds = describe_ax(AX, window).bounds if window else None
        window_changed = bool(capture and (
            not final_bounds or not same_window_bounds(capture.bounds, {
                'X': final_bounds.x, 'Y': final_bounds.y,
                'Width': final_bounds.width, 'Height': final_bounds.height,
            })
        ))
        if window_changed:
            records, self.handles = [], {}
            capture = None
            capture_error = 'WindowChangedDuringObservation'
        ocr_trigger_reasons = degradation_reasons(records, traversal_error=traversal_error)
        fallback_reasons = list(ocr_trigger_reasons)
        ocr_status = 'not_needed'
        if capture_error:
            fallback_reasons.append('window_changed_during_observation' if window_changed
                                    else 'window_capture_unavailable')
        elif ocr_trigger_reasons:
            ocr_status = 'attempted'
            try:
                ocr_items = await asyncio.to_thread(recognize_text, capture)
                records = fuse_ax_ocr(records, ocr_items, window_id=capture.window_id)
                fallback_reasons = degradation_reasons(records, traversal_error=traversal_error)
                ocr_status = 'fused' if any('ocr' in record.get('sources', ()) for record in records) else 'empty'
                if not ocr_items:
                    fallback_reasons.append('ocr_empty')
            except Exception:
                ocr_status = 'unavailable'
                fallback_reasons.append('ocr_unavailable')
        fallback_reasons = list(dict.fromkeys(fallback_reasons))
        self.records = {record['index']: record for record in records}
        self.sid = uuid4().hex
        if capture:
            self._captures[self.sid] = capture
            self._windows[self.sid] = window
            while len(self._captures) > 4:
                expired = next(iter(self._captures))
                self._captures.pop(expired)
                self._windows.pop(expired, None)
        authority = ({'pid': capture.pid, 'window_id': capture.window_id,
                      'bounds': capture.bounds, 'scale': capture.scale}
                     if capture else {'pid': self._pid(), 'window_id': None, 'bounds': None})
        unresolved = [
            {'reason': reason,
             'stage': ('capture' if reason in {'window_capture_unavailable',
                                               'window_changed_during_observation'} else
                       'ocr' if reason.startswith('ocr_') else 'accessibility')}
            for reason in fallback_reasons
        ]
        return BrowserSnapshot(snapshot_id=self.sid, root_id=f"macos-pid-{self._pid()}", url='', title=title,
                               text='\n'.join(e['name'] + ' ' + str(e.get('value') or '') for e in records),
                               elements=records, source='macos-accessibility', identity={
                                   'pid': self._pid(),
                                   'application': self.context.source_application,
                                   'window_title': title,
                                   'window_authority': authority,
                                   'perception': ('ax+ocr' if any('ocr' in record.get('sources', ())
                                                                  for record in records) else 'ax'),
                                   'ocr': {'status': ocr_status, 'trigger_reasons': ocr_trigger_reasons},
                                   'fallback_reasons': fallback_reasons,
                                   'unresolved_degradation': unresolved,
                                   'capture_error': capture_error,
                               })

    async def find(self, *, role='', name='', value=''):
        """Return semantic native matches from one fresh window-scoped observation."""
        snapshot = await self.snapshot()
        matches = [record for record in snapshot.elements
                   if (not role or record.get('role') == role)
                   and (not name or name.casefold() in str(record.get('name') or '').casefold())
                   and (not value or value.casefold() in str(record.get('value') or '').casefold())]
        return {'state_id': snapshot.snapshot_id, 'root_id': snapshot.root_id, 'matches': matches}

    async def wait_for(self, predicate, *, before=None, initial=None):
        """Wait on the shared tri-state predicate without delivering input."""
        from mcp_vision.state import compile_state
        from mcp_vision.verification import DEFAULT_VERIFIER, VerificationPredicate
        predicate = VerificationPredicate.model_validate(predicate)
        epoch = before.epoch + 1 if before else 1

        async def observe():
            return compile_state(await self.snapshot(), epoch=epoch)

        return await DEFAULT_VERIFIER.wait(observe, predicate, before=before, initial=initial)

    async def settle(self, operation=''):
        """Compatibility no-op; semantic predicate waits own readiness timing."""
        return None

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
        trace = ExecutionTrace()
        try:
            import ApplicationServices as AX
            visual = bool(self.records.get(index, {}).get('ocr_only'))
            preflight = await self._preflight(snapshot_id, index, AX, require_element=not visual)
            trace.bind_authority(preflight.authority)
        except NativePreflightError as exc:
            return self._refused('click', trace, exc)
        try:
            from mcp_vision.macos_ui import _ax_copy
            if visual:
                if not self._authorize_foreground('click', preflight.record, trace):
                    return Receipt(status='blocked', action='click', executed=False,
                                   message='Foreground visual click was not authorized.', evidence=trace.evidence())
                preflight = await self._preflight(snapshot_id, index, AX, require_element=False)
                trace.bind_authority(preflight.authority)
                if preflight.capture.image.tobytes() != preflight.observed_capture.image.tobytes():
                    raise NativePreflightError(
                        RefusalReason.PIXELS_CHANGED,
                        'The exact target-window pixels changed after observation.')
                if not await self._focus_exact_window(AX, preflight, trace):
                    return Receipt(status='blocked', action='click', executed=False,
                                   message='Could not focus the exact authorized window.', evidence=trace.evidence())
                record = preflight.record
                point = (int(record['x'] + record['w'] / 2), int(record['y'] + record['h'] / 2))
                bounds = preflight.capture.bounds
                if not (bounds['X'] <= point[0] <= bounds['X'] + bounds['Width']
                        and bounds['Y'] <= point[1] <= bounds['Y'] + bounds['Height']):
                    raise NativePreflightError(RefusalReason.TARGET_CHANGED,
                                               'The visual target is outside the exact window.')
                trace.add(ExecutionMethod.VISUAL_POINTER, AttemptOutcome.UNKNOWN, background=False,
                          detail='Pointer dispatched against unchanged exact-window pixels',
                          evidence={'point': point, 'visual_identity': record.get('identity', {}).get('visual')})
                trace.refuse(RefusalReason.DELIVERY_UNKNOWN_NO_FALLBACK)
                trace.observe(status='required', reason='pointer_delivery_has_no_semantic_read_back')
                from mcp_vision.core.actuate import get_actuator
                try:
                    get_actuator().click(*point)
                except Exception as exc:
                    return Receipt(
                        status='unverified', action='click', executed=None,
                        message='Visual pointer delivery is unknown; no replay was attempted.',
                        evidence={**trace.evidence(), 'dispatch_error': type(exc).__name__})
                return Receipt(status='unverified', action='click', executed=True,
                               message='Visual pointer dispatched once; verify the successor state.',
                               evidence=trace.evidence())

            element = preflight.element
            actions = [str(a) for a in (_ax_copy(AX, element, 'AXActions') or [])]
            if 'AXPress' in actions:
                err = AX.AXUIElementPerformAction(element, 'AXPress')
                if err == 0:
                    trace.add(ExecutionMethod.AX_BACKGROUND, AttemptOutcome.UNKNOWN, background=True,
                              detail='AXPress accepted without activating the application')
                    trace.refuse(RefusalReason.DELIVERY_UNKNOWN_NO_FALLBACK)
                    trace.observe(status='required', reason='AXPress_has_no_immediate_postcondition')
                    return Receipt(status='unverified', action='click', executed=True,
                                   message='Pressed via background Accessibility; verify the successor state.',
                                   evidence=trace.evidence())
                trace.add(ExecutionMethod.AX_BACKGROUND, AttemptOutcome.DIDNT, background=True,
                           detail=f'AXPress returned {err}')
            else:
                trace.add(ExecutionMethod.AX_BACKGROUND, AttemptOutcome.DIDNT, background=True,
                          detail='AXPress is not advertised by the target')
            if not trace.can_fallback:
                trace.refuse(RefusalReason.DELIVERY_UNKNOWN_NO_FALLBACK)
                return Receipt(status='unverified', action='click', executed=True,
                               message='Delivery is unknown; no fallback was attempted.', evidence=trace.evidence())
            if not self._authorize_foreground('click', preflight.record, trace):
                return Receipt(status='blocked', action='click', executed=False,
                               message='Foreground escalation was not authorized.', evidence=trace.evidence())
            preflight = await self._preflight(snapshot_id, index, AX)
            trace.bind_authority(preflight.authority)
            element = preflight.element
            if not await self._focus_exact_window(AX, preflight, trace):
                return Receipt(status='blocked', action='click', executed=False,
                               message='Could not focus the exact authorized window.', evidence=trace.evidence())
            if 'AXPress' in actions:
                err = AX.AXUIElementPerformAction(element, 'AXPress')
                if err == 0:
                    trace.add(ExecutionMethod.AX_FOREGROUND, AttemptOutcome.UNKNOWN, background=False,
                              detail='AXPress dispatched after foreground activation')
                    trace.refuse(RefusalReason.DELIVERY_UNKNOWN_NO_FALLBACK)
                    trace.observe(status='required', reason='AXPress_has_no_immediate_postcondition')
                    return Receipt(status='unverified', action='click', executed=True,
                                   message='Pressed via foreground Accessibility; verify the successor state.',
                                   evidence=trace.evidence())
                trace.add(ExecutionMethod.AX_FOREGROUND, AttemptOutcome.DIDNT, background=False,
                           detail=f'AXPress returned {err}')
            if not trace.can_fallback:
                trace.refuse(RefusalReason.DELIVERY_UNKNOWN_NO_FALLBACK)
                return Receipt(status='unverified', action='click', executed=True,
                               message='Delivery is unknown; pointer fallback was not attempted.',
                               evidence=trace.evidence())
            desc = describe_ax(AX, element)
            box = desc.bounds
            if not box:
                return Receipt(status='error', action='click', message='No bounds for native click.', executed=False,
                               evidence=trace.evidence())
            point = (int(box.x + box.width / 2), int(box.y + box.height / 2))
            trace.add(ExecutionMethod.FOREGROUND_POINTER, AttemptOutcome.UNKNOWN, background=False,
                      detail='Pointer fallback dispatched at fresh AX bounds', evidence={'point': point})
            trace.refuse(RefusalReason.DELIVERY_UNKNOWN_NO_FALLBACK)
            trace.observe(status='required', reason='pointer_delivery_has_no_semantic_read_back')
            from mcp_vision.core.actuate import get_actuator
            try:
                get_actuator().click(*point)
            except Exception as exc:
                return Receipt(
                    status='unverified', action='click', executed=None,
                    message='Pointer delivery is unknown; no replay was attempted.',
                    evidence={**trace.evidence(), 'dispatch_error': type(exc).__name__})
            return Receipt(status='unverified', action='click', executed=True,
                           message='Clicked via foreground pointer fallback; verify the successor state.',
                           evidence=trace.evidence())
        except NativePreflightError as exc:
            return self._refused('click', trace, exc)
        except Exception as exc:
            return Receipt(status='error', action='click', message=redact(str(exc)), executed=None,
                           evidence=trace.evidence())

    async def fill(self, snapshot_id, index, text):
        import asyncio

        if not self.allow_writes:
            return self._blocked('fill')
        trace = ExecutionTrace()
        try:
            import ApplicationServices as AX
            preflight = await self._preflight(snapshot_id, index, AX)
            trace.bind_authority(preflight.authority)
        except NativePreflightError as exc:
            return self._refused('fill', trace, exc)
        try:
            element = preflight.element
            AX.AXUIElementSetAttributeValue(element, 'AXFocused', True)
            err = AX.AXUIElementSetAttributeValue(element, 'AXValue', str(text))
            if err == 0:
                # Some editors acknowledge AXValue and discard it on their next UI cycle.
                await asyncio.sleep(0.25)
                fresh = describe_ax(AX, element)
                matches = (fresh.value or '') == str(text)
                trace.add(ExecutionMethod.AX_BACKGROUND,
                          AttemptOutcome.WORKED if matches else AttemptOutcome.UNKNOWN,
                          background=True, detail='AXValue write', evidence={'value_matches': matches})
                trace.observe(status='observed', value=fresh.value, value_matches=matches)
                if matches:
                    return Receipt(status='verified', action='fill', executed=True,
                                   message='Filled via background Accessibility.',
                                   evidence={**trace.evidence(), 'value': fresh.value})
                trace.refuse(RefusalReason.DELIVERY_UNKNOWN_NO_FALLBACK)
                return Receipt(status='unverified', action='fill', executed=True,
                               message='AXValue was accepted but read-back is inconclusive; no fallback was attempted.',
                               evidence={**trace.evidence(), 'value': fresh.value})
            else:
                trace.add(ExecutionMethod.AX_BACKGROUND, AttemptOutcome.DIDNT, background=True,
                           detail=f'AXValue returned {err}')
            if not preflight.keyboard_unambiguous:
                trace.add(ExecutionMethod.PID_KEYBOARD, AttemptOutcome.DIDNT, background=True,
                          detail='PID destination is not one proven window', evidence={
                              'refusal_reason': RefusalReason.SAME_PID_SIBLING_AMBIGUOUS.value,
                              'visible_window_ids': list(preflight.sibling_window_ids),
                          })
            else:
                try:
                    from mcp_vision.macos_input import TargetedInputUnavailable, targeted_replace_text
                    dispatch = targeted_replace_text(self._pid(), str(text))
                    await asyncio.sleep(0.25)
                    fresh = describe_ax(AX, element)
                    matches = (fresh.value or '') == str(text)
                    trace.add(ExecutionMethod.PID_KEYBOARD,
                              AttemptOutcome.WORKED if matches else AttemptOutcome.UNKNOWN,
                              background=True, detail='PID-targeted replace text', evidence={
                                  'value_matches': matches, 'events_posted': dispatch.events_posted})
                    trace.observe(status='observed', value=fresh.value, value_matches=matches)
                    if not matches:
                        trace.refuse(RefusalReason.DELIVERY_UNKNOWN_NO_FALLBACK)
                    return Receipt(status='verified' if matches else 'unverified', action='fill', executed=True,
                                   message='Filled via PID-targeted keyboard.' if matches
                                   else 'PID input was dispatched once; no fallback was attempted.',
                                   evidence={**trace.evidence(), 'value': fresh.value})
                except TargetedInputUnavailable as targeted_error:
                    outcome = (AttemptOutcome.UNKNOWN if targeted_error.delivery_unknown
                               else AttemptOutcome.DIDNT)
                    trace.add(ExecutionMethod.PID_KEYBOARD, outcome, background=True,
                              detail=type(targeted_error).__name__,
                              evidence={'events_posted': targeted_error.events_posted})
                    if outcome is AttemptOutcome.UNKNOWN:
                        trace.refuse(RefusalReason.DELIVERY_UNKNOWN_NO_FALLBACK)
                        return Receipt(status='unverified', action='fill', executed=True,
                                       message='PID input delivery is unknown; no fallback was attempted.',
                                       evidence=trace.evidence())
            if not self._authorize_foreground('fill', preflight.record, trace):
                if len(preflight.sibling_window_ids) > 1:
                    trace.refuse(RefusalReason.SAME_PID_SIBLING_AMBIGUOUS)
                return Receipt(status='blocked', action='fill', executed=False,
                               message='No unambiguous background route and foreground escalation was not authorized.',
                               evidence=trace.evidence())
            preflight = await self._preflight(snapshot_id, index, AX)
            trace.bind_authority(preflight.authority)
            element = preflight.element
            if not await self._focus_exact_window(AX, preflight, trace):
                return Receipt(status='blocked', action='fill', executed=False,
                               message='Could not focus the exact authorized window.', evidence=trace.evidence())
            err = AX.AXUIElementSetAttributeValue(element, 'AXValue', str(text))
            if err == 0:
                await asyncio.sleep(0.25)
                fresh = describe_ax(AX, element)
                matches = (fresh.value or '') == str(text)
                trace.add(ExecutionMethod.AX_FOREGROUND,
                          AttemptOutcome.WORKED if matches else AttemptOutcome.UNKNOWN,
                          background=False, detail='AXValue after foreground activation')
                trace.observe(status='observed', value=fresh.value, value_matches=matches)
                if not matches:
                    trace.refuse(RefusalReason.DELIVERY_UNKNOWN_NO_FALLBACK)
                return Receipt(status='verified' if matches else 'unverified', action='fill', executed=True,
                               message='Filled via foreground Accessibility.' if matches
                               else 'Foreground AXValue dispatched; read-back did not match.',
                               evidence={**trace.evidence(), 'value': fresh.value})
            trace.add(ExecutionMethod.AX_FOREGROUND, AttemptOutcome.DIDNT, background=False,
                      detail=f'AXValue returned {err}')
            if not trace.can_fallback:
                trace.refuse(RefusalReason.DELIVERY_UNKNOWN_NO_FALLBACK)
                return Receipt(status='unverified', action='fill', executed=True,
                               message='Delivery is unknown; keyboard fallback was not attempted.',
                               evidence=trace.evidence())
            trace.add(ExecutionMethod.FOREGROUND_KEYBOARD, AttemptOutcome.UNKNOWN, background=False,
                      detail='Global keyboard fallback dispatched')
            trace.refuse(RefusalReason.DELIVERY_UNKNOWN_NO_FALLBACK)
            trace.observe(status='required', reason='keyboard_delivery_has_no_semantic_read_back')
            from mcp_vision.core.actuate import get_actuator
            try:
                get_actuator().type_text(str(text), press_enter=False)
            except Exception as exc:
                return Receipt(
                    status='unverified', action='fill', executed=None,
                    message='Keyboard delivery is unknown; no replay was attempted.',
                    evidence={**trace.evidence(), 'dispatch_error': type(exc).__name__})
            return Receipt(status='unverified', action='fill', executed=True,
                           message='Foreground keyboard input dispatched; re-observe before retrying.',
                           evidence=trace.evidence())
        except NativePreflightError as exc:
            return self._refused('fill', trace, exc)
        except Exception as exc:
            return Receipt(status='error', action='fill', message=redact(str(exc)), executed=None,
                           evidence=trace.evidence())

    async def select(self, snapshot_id, index, value):
        import asyncio

        if not self.allow_writes:
            return self._blocked('select')
        trace = ExecutionTrace()
        try:
            import ApplicationServices as AX
            preflight = await self._preflight(snapshot_id, index, AX)
            trace.bind_authority(preflight.authority)
        except NativePreflightError as exc:
            return self._refused('select', trace, exc)
        try:
            element = preflight.element
            err = AX.AXUIElementSetAttributeValue(element, 'AXValue', str(value))
            if err == 0:
                await asyncio.sleep(0.25)
                fresh = describe_ax(AX, element)
                matches = (fresh.value or '') == str(value)
                trace.add(ExecutionMethod.AX_BACKGROUND,
                          AttemptOutcome.WORKED if matches else AttemptOutcome.UNKNOWN,
                          background=True, detail='AXValue selection',
                          evidence={'value_matches': matches})
                trace.observe(status='observed', value=fresh.value, value_matches=matches)
                if not matches:
                    trace.refuse(RefusalReason.DELIVERY_UNKNOWN_NO_FALLBACK)
                return Receipt(
                    status='verified' if matches else 'unverified', action='select', executed=True,
                    message='Selected via background Accessibility.' if matches else
                    'AXValue selection was accepted but read-back is inconclusive; no fallback was attempted.',
                    evidence={**trace.evidence(), 'value': fresh.value})
            trace.add(ExecutionMethod.AX_BACKGROUND, AttemptOutcome.DIDNT, background=True,
                      detail=f'AXValue returned {err}')

            # AXPress is still semantic and window-bound. It may expose the
            # choices, but its effect cannot prove which value was selected.
            from mcp_vision.macos_ui import _ax_copy
            actions = [str(action) for action in (_ax_copy(AX, element, 'AXActions') or [])]
            if 'AXPress' in actions:
                press_err = AX.AXUIElementPerformAction(element, 'AXPress')
                outcome = AttemptOutcome.UNKNOWN if press_err == 0 else AttemptOutcome.DIDNT
                trace.add(ExecutionMethod.AX_BACKGROUND, outcome, background=True,
                          detail=f'AXPress returned {press_err}')
                if outcome is AttemptOutcome.UNKNOWN:
                    trace.refuse(RefusalReason.DELIVERY_UNKNOWN_NO_FALLBACK)
                    trace.observe(status='required', reason='control_opened_without_proven_value')
                    return Receipt(
                        status='unverified', action='select', executed=True,
                        message=f'Opened the native control for {value!r}; no second action was replayed.',
                        evidence=trace.evidence())
            else:
                trace.add(ExecutionMethod.AX_BACKGROUND, AttemptOutcome.DIDNT, background=True,
                          detail='AXPress is not advertised by the target')
            return Receipt(status='error', action='select', executed=False,
                           message='The native control supports neither AXValue nor AXPress.',
                           evidence=trace.evidence())
        except NativePreflightError as exc:
            return self._refused('select', trace, exc)
        except Exception as exc:
            return Receipt(status='error', action='select', message=redact(str(exc)), executed=None,
                           evidence=trace.evidence())

    async def set_checked(self, snapshot_id, index, checked: bool):
        if not self.allow_writes:
            return self._blocked('set_checked')
        trace = ExecutionTrace()
        try:
            import ApplicationServices as AX
            preflight = await self._preflight(snapshot_id, index, AX)
            trace.bind_authority(preflight.authority)
        except NativePreflightError as exc:
            return self._refused('set_checked', trace, exc)
        try:
            element = preflight.element
            current = describe_ax(AX, element).attributes.get('checked') == 'True'
            if current is bool(checked):
                trace.observe(status='observed', checked=current, checked_matches=True)
                return Receipt(status='verified', action='set_checked', executed=False,
                               message='Checkbox already in the requested state.',
                               evidence={**trace.evidence(), 'checked': current})
            err = AX.AXUIElementPerformAction(element, 'AXPress')
            after = describe_ax(AX, element).attributes.get('checked') == 'True'
            outcome = (AttemptOutcome.DIDNT if err != 0 else
                       AttemptOutcome.WORKED if after is bool(checked) else AttemptOutcome.UNKNOWN)
            trace.add(ExecutionMethod.AX_BACKGROUND, outcome, background=True,
                      detail=f'AXPress returned {err}', evidence={'checked': after})
            trace.observe(status='observed', checked=after, checked_matches=after is bool(checked))
            if err == 0:
                if outcome is AttemptOutcome.UNKNOWN:
                    trace.refuse(RefusalReason.DELIVERY_UNKNOWN_NO_FALLBACK)
                return Receipt(status='verified' if outcome is AttemptOutcome.WORKED else 'unverified',
                               action='set_checked', executed=True,
                               message='Updated native checkbox.' if outcome is AttemptOutcome.WORKED
                               else 'AXPress was accepted; no fallback was attempted.',
                               evidence={**trace.evidence(), 'checked': after})
            if not self._authorize_foreground('set checked', preflight.record, trace):
                return Receipt(status='blocked', action='set_checked', executed=False,
                               message='Foreground escalation was not authorized.', evidence=trace.evidence())
            preflight = await self._preflight(snapshot_id, index, AX)
            trace.bind_authority(preflight.authority)
            element = preflight.element
            if not await self._focus_exact_window(AX, preflight, trace):
                return Receipt(status='blocked', action='set_checked', executed=False,
                               message='Could not focus the exact authorized window.', evidence=trace.evidence())
            err = AX.AXUIElementPerformAction(element, 'AXPress')
            after = describe_ax(AX, element).attributes.get('checked') == 'True'
            outcome = (AttemptOutcome.DIDNT if err != 0 else
                       AttemptOutcome.WORKED if after is bool(checked) else AttemptOutcome.UNKNOWN)
            trace.add(ExecutionMethod.AX_FOREGROUND, outcome, background=False,
                      detail=f'AXPress returned {err}', evidence={'checked': after})
            trace.observe(status='observed', checked=after, checked_matches=after is bool(checked))
            if outcome is AttemptOutcome.UNKNOWN:
                trace.refuse(RefusalReason.DELIVERY_UNKNOWN_NO_FALLBACK)
            return Receipt(status='verified' if outcome is AttemptOutcome.WORKED else
                           'error' if outcome is AttemptOutcome.DIDNT else 'unverified',
                           action='set_checked', executed=err == 0,
                           message='Updated native checkbox.' if outcome is AttemptOutcome.WORKED
                           else 'Checkbox action failed.' if outcome is AttemptOutcome.DIDNT
                           else 'Foreground AXPress was accepted; no further fallback was attempted.',
                           evidence={**trace.evidence(), 'checked': after})
        except NativePreflightError as exc:
            return self._refused('set_checked', trace, exc)
        except Exception as exc:
            return Receipt(status='error', action='set_checked', message=redact(str(exc)), executed=None,
                           evidence=trace.evidence())

    async def upload(self, snapshot_id, index, path):
        return self._blocked('upload', 'Native file upload is not supported in this backend.')

    async def scroll(self, snapshot_id, delta_y):
        trace = ExecutionTrace()
        if snapshot_id != self.sid:
            trace.refuse(RefusalReason.STALE_CAPTURE)
            return Receipt(status='stale', action='scroll', message='Snapshot expired.', executed=False,
                           evidence=trace.evidence())
        try:
            import ApplicationServices as AX
            index = next(iter(self.records), None)
            if index is None:
                raise NativePreflightError(RefusalReason.WINDOW_AUTHORITY_MISSING,
                                           'No exact native window target is available.')
            preflight = await self._preflight(snapshot_id, index, AX, require_element=False)
            trace.bind_authority(preflight.authority)
            keycode = (121 if delta_y > 0 else 116) if abs(delta_y) >= 400 else (125 if delta_y > 0 else 126)
            if preflight.keyboard_unambiguous:
                try:
                    from mcp_vision.macos_input import TargetedInputUnavailable, targeted_key
                    dispatch = targeted_key(self._pid(), keycode)
                    trace.add(ExecutionMethod.PID_KEYBOARD, AttemptOutcome.UNKNOWN, background=True,
                              detail='PID-targeted scroll key dispatched',
                              evidence={'events_posted': dispatch.events_posted})
                    trace.refuse(RefusalReason.DELIVERY_UNKNOWN_NO_FALLBACK)
                    return Receipt(status='unverified', action='scroll', executed=True,
                                   message='Scrolled with PID-targeted keyboard once; verify the successor state.',
                                   evidence=trace.evidence())
                except TargetedInputUnavailable as targeted_error:
                    outcome = (AttemptOutcome.UNKNOWN if targeted_error.delivery_unknown
                               else AttemptOutcome.DIDNT)
                    trace.add(ExecutionMethod.PID_KEYBOARD, outcome, background=True,
                              detail=type(targeted_error).__name__,
                              evidence={'events_posted': targeted_error.events_posted})
                    if outcome is AttemptOutcome.UNKNOWN:
                        trace.refuse(RefusalReason.DELIVERY_UNKNOWN_NO_FALLBACK)
                        return Receipt(status='unverified', action='scroll', executed=True,
                                       message='PID scroll delivery is unknown; no fallback was attempted.',
                                       evidence=trace.evidence())
            else:
                trace.add(ExecutionMethod.PID_KEYBOARD, AttemptOutcome.DIDNT, background=True,
                          detail='PID destination is not one proven window', evidence={
                              'refusal_reason': RefusalReason.SAME_PID_SIBLING_AMBIGUOUS.value,
                              'visible_window_ids': list(preflight.sibling_window_ids),
                          })
            if not self._authorize_foreground('scroll', preflight.record, trace):
                if len(preflight.sibling_window_ids) > 1:
                    trace.refuse(RefusalReason.SAME_PID_SIBLING_AMBIGUOUS)
                return Receipt(status='blocked', action='scroll', executed=False,
                               message='No unambiguous background route and foreground escalation was not authorized.',
                               evidence=trace.evidence())
            preflight = await self._preflight(snapshot_id, index, AX, require_element=False)
            trace.bind_authority(preflight.authority)
            if not await self._focus_exact_window(AX, preflight, trace):
                return Receipt(status='blocked', action='scroll', executed=False,
                               message='Could not focus the exact authorized window.', evidence=trace.evidence())
            key = ('pagedown' if delta_y > 0 else 'pageup') if abs(delta_y) >= 400 else ('down' if delta_y > 0 else 'up')
            trace.add(ExecutionMethod.FOREGROUND_KEYBOARD, AttemptOutcome.UNKNOWN, background=False,
                      detail='Global scroll key dispatched')
            trace.refuse(RefusalReason.DELIVERY_UNKNOWN_NO_FALLBACK)
            trace.observe(status='required', reason='keyboard_delivery_has_no_semantic_read_back')
            from mcp_vision.core.actuate import get_actuator
            try:
                get_actuator().press([key])
            except Exception as exc:
                return Receipt(
                    status='unverified', action='scroll', executed=None,
                    message='Scroll-key delivery is unknown; no replay was attempted.',
                    evidence={**trace.evidence(), 'dispatch_error': type(exc).__name__})
            return Receipt(status='unverified', action='scroll', executed=True,
                            message='Scrolled with foreground keyboard fallback; verify the successor state.',
                            evidence=trace.evidence())
        except NativePreflightError as exc:
            return self._refused('scroll', trace, exc)
        except Exception as exc:
            return Receipt(status='error', action='scroll', message=redact(str(exc)), executed=None,
                           evidence=trace.evidence())

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
        if action == 'select':
            return await self.select(snap.snapshot_id, index, text)
        if action == 'set_checked':
            normalized = str(text).strip().casefold()
            if normalized not in {'true', 'false'}:
                return Receipt(status='blocked', action=action,
                               message='set_checked requires text to be true or false.', executed=False)
            return await self.set_checked(snap.snapshot_id, index, normalized == 'true')
        return Receipt(status='blocked', action=action, message='Unsupported native action.')

    async def screenshot(self):
        capture = self._captures.get(self.sid)
        return capture.png if capture else b''

    async def close(self):
        await self.clear_highlight()

"""Model-free browser runtime. The host owns planning; we own target validity.

One instance owns one isolated browser context. No provider SDK, arbitrary JS
tool, or approval parameter is exposed to the model. Successful dispatch is
never promoted to task completion.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from mcp_vision.core.governor import Governor, classify
from mcp_vision.redaction import redact
from mcp_vision.core.models import BoundingBox, Policy, ScreenElement
from phase2_mcp.page_snapshot import SNAPSHOT_JS
from phase2_mcp.cdp_snapshot import PersistentCDPSnapshotter


class Receipt(BaseModel):
    action_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    status: Literal["verified", "unverified", "blocked", "stale", "error"]
    action: str
    message: str
    executed: bool | None = False
    task_complete: bool = False
    evidence: dict = Field(default_factory=dict)


class BrowserSnapshot(BaseModel):
    snapshot_id: str
    root_id: str = ""
    url: str
    title: str
    text: str
    elements: list[dict]
    pruned: dict = Field(default_factory=dict)
    facts: list[dict] = Field(default_factory=list)
    identity: dict = Field(default_factory=dict)
    completeness: dict = Field(default_factory=dict)
    viewport: dict = Field(default_factory=dict)
    protocol: dict = Field(default_factory=dict)
    guard: dict = Field(default_factory=dict)
    source: str = "dom-accessibility"


@dataclass
class Target:
    handle: object | None
    signature: str
    record: dict
    backend_node_id: int | None = None
    object_id: str | None = None


class _CDPHandle:
    """Compatibility shim; identity and dispatch still stay on backendNodeId."""
    def __init__(self, runtime, target):
        self.runtime, self.target = runtime, target

    async def evaluate(self, expression, *_args):
        if "el.form" in expression and "submit" in expression:
            return await self.runtime._cdp_call(
                self.target, "function(){return !!this.form&&['submit','image'].includes(this.type)}")
        raise RuntimeError("unsupported compatibility evaluation")

    async def click(self, **_kwargs):
        await self.runtime._cdp_click(self.target)

    async def dispose(self):
        return None


_STATE_JS = r"""el => ({
  connected: el.isConnected && el.ownerDocument === document,
  html: el.outerHTML,
  tag: el.tagName.toLowerCase(), type: (el.type || '').toLowerCase(),
  value: el.value, checked: el.checked, selectedIndex: el.selectedIndex,
  options: el.options ? Array.from(el.options).map(o => ({
    value: o.value, label: o.textContent.trim(), disabled: o.disabled
  })) : [],
  labels: el.labels ? Array.from(el.labels).map(n => n.textContent) : [],
  domId: el.id || null,
  testId: el.getAttribute('data-testid') || el.getAttribute('data-test') || null,
  fieldName: el.getAttribute('name') || null,
  labelledBy: (el.getAttribute('aria-labelledby') || '').split(/\s+/)
    .map(id => document.getElementById(id)?.textContent || ''),
  form: el.form ? {action: el.form.action, method: el.form.method} : null,
  x: el.getBoundingClientRect().x, y: el.getBoundingClientRect().y,
  w: el.getBoundingClientRect().width, h: el.getBoundingClientRect().height
})"""
_REACHABLE_JS = """el => {
  if (!el.isConnected || el.matches(':disabled') || el.disabled || el.closest('[inert]') || el.getAttribute('aria-disabled') === 'true') return false;
  const r = el.getBoundingClientRect();
  const x = r.x + r.width / 2, y = r.y + r.height / 2;
  const top = document.elementFromPoint(x, y);
  return !!top && (top === el || el.contains(top));
}"""


def _signature(state: dict) -> str:
    import json
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def origin(url: str) -> str:
    p = urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ValueError("only HTTP(S) URLs without embedded credentials are allowed")
    port = p.port
    suffix = f":{port}" if port and port != (443 if p.scheme == "https" else 80) else ""
    host = f"[{p.hostname}]" if ":" in p.hostname else p.hostname
    return f"{p.scheme}://{host}{suffix}"


class BrowserRuntime:
    def __init__(self, *, page=None, allow_writes=False, allowed_origins=(),
                 governor=None, headless=True, snapshot_ttl=30, channel=None):
        self.page = page
        self.allow_writes = allow_writes
        self.allowed_origins = frozenset(origin(x) for x in allowed_origins)
        self.governor = governor or Governor()
        self.headless = headless
        self.channel = channel
        self.snapshot_ttl = snapshot_ttl
        self._playwright = self._browser = self._context = None
        self._snapshot = None
        self._targets = {}
        self._observed_at = 0.0
        self._cdp_session = self._cdp_page = self._snapshotter = None
        self._successor_ready = False
        self._lock = asyncio.Lock()
        self._root_id = f"browser-{uuid.uuid4().hex[:16]}"

    def _check_url(self, url):
        value = origin(url)
        if self.allowed_origins and value not in self.allowed_origins:
            raise ValueError(f"origin is outside this runtime's scope: {value}")

    async def _ensure(self):
        if self.page is not None:
            return
        from playwright.async_api import async_playwright
        self._playwright = await async_playwright().start()
        try:
            launch = {"headless": self.headless, "args": ["--disable-blink-features=AutomationControlled"]}
            if self.channel:
                launch["channel"] = self.channel
            self._browser = await self._playwright.chromium.launch(**launch)
            self._context = await self._browser.new_context(
                accept_downloads=False, service_workers="block",
                viewport={"width": 1280, "height": 900},
                user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
            )
            if self.allowed_origins:
                async def scoped_route(route):
                    try:
                        self._check_url(route.request.url)
                    except ValueError:
                        await route.abort()
                    else:
                        await route.continue_()
                await self._context.route("**/*", scoped_route)
            self.page = await self._context.new_page()
            self.page.set_default_timeout(5000)
        except Exception:
            await self._playwright.stop()
            self._playwright = None
            raise

    async def _invalidate(self):
        for target in self._targets.values():
            try:
                await target.handle.dispose()
            except Exception:
                pass
        self._targets = {}
        self._snapshot = None
        self._successor_ready = False

    async def _detach_cdp(self):
        session = self._cdp_session
        self._cdp_session = self._cdp_page = self._snapshotter = None
        if session is not None:
            try:
                await session.detach()
            except Exception:
                pass

    async def _ensure_cdp(self):
        if self._cdp_session is not None and self._cdp_page is self.page:
            return self._snapshotter
        await self._detach_cdp()
        context = getattr(self.page, "context", None)
        if context is None or not hasattr(context, "new_cdp_session"):
            return None
        self._cdp_session = await context.new_cdp_session(self.page)
        self._cdp_page = self.page
        self._snapshotter = PersistentCDPSnapshotter(self._cdp_session)
        return self._snapshotter

    async def navigate(self, url: str) -> Receipt:
        async with self._lock:
            executed = False
            try:
                self._check_url(url)
                await self._ensure()
                await self._invalidate()
                executed = None
                await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
                self._check_url(self.page.url)
                return Receipt(status="verified", action="navigate", executed=True,
                               message="Navigation observed; inspect the page before acting.",
                               evidence={"url": self.page.url, "execution_path": "dom", "background": True})
            except Exception as e:
                return Receipt(status="error", action="navigate", executed=executed, message=redact(str(e)))

    async def _legacy_snapshot(self) -> BrowserSnapshot:
            raw = await self.page.evaluate(SNAPSHOT_JS, 60)
            records = []
            for rec in raw["elements"]:
                handle = await self.page.query_selector(f'[data-agent-index="{rec["index"]}"]')
                if handle is None:
                    continue
                state = await handle.evaluate(_STATE_JS)
                if not state["connected"]:
                    await handle.dispose()
                    continue
                if state["tag"] == "select":
                    rec["value"] = state["value"]
                    rec["options"] = state["options"]
                elif state["type"] in {"checkbox", "radio"}:
                    rec["checked"] = bool(state["checked"])
                elif state["type"] == "file":
                    rec["input_type"] = "file"
                rec["submits"] = bool(state.get("form") and state.get("type") in {"submit", "image"})
                identity = state.get("domId") or state.get("testId") or state.get("fieldName")
                if identity:
                    rec["identity"] = {"dom": f'{state["tag"]}:{identity}'}
                self._targets[rec["index"]] = Target(handle, _signature(state), rec)
                records.append(rec)
            text = await self.page.locator("body").inner_text(timeout=5000)
            self._snapshot = BrowserSnapshot(snapshot_id=uuid.uuid4().hex, root_id=self._root_id,
                url=self.page.url, title=await self.page.title(), text=text[:12000],
                elements=records, pruned=raw.get("pruned", {}),
                facts=raw.get("facts") or [], identity=raw.get("identity") or {})
            self._observed_at = time.monotonic()
            return self._snapshot

    async def _cdp_snapshot(self, snapshotter) -> BrowserSnapshot:
        before = snapshotter.budget.total
        raw = await snapshotter.capture()
        records = raw.get("elements") or []
        self._targets = {}
        for rec in records:
            target = Target(None, _signature({
                key: rec.get(key) for key in
                ("identity", "role", "name", "x", "y", "w", "h", "input_type", "value", "checked")
            }), rec, int(rec["backendNodeId"]))
            target.handle = _CDPHandle(self, target)
            self._targets[int(rec["index"])] = target
        try:
            text = await self.page.locator("body").inner_text(timeout=5000)
        except Exception:
            text = ""
        url = raw.get("url") or self.page.url
        title = raw.get("title") or await self.page.title()
        self._snapshot = BrowserSnapshot(
            snapshot_id=uuid.uuid4().hex, url=url, title=title, text=text[:12000],
            elements=records, pruned=raw.get("pruned") or {},
            completeness=raw.get("completeness") or {}, viewport=raw.get("viewport") or {},
            protocol={"calls": raw.get("protocol_calls", 0) - before,
                      "detail": raw.get("protocol_call_detail") or {}, "screenshot": False},
            guard={"url": url, "root": raw.get("root_backend_node_id"),
                   "focus": raw.get("focus") or ""},
            source="cdp-dom-snapshot+full-ax",
        )
        self._observed_at = time.monotonic()
        return self._snapshot

    async def _observe_locked(self) -> BrowserSnapshot:
        await self._ensure()
        if self.page.url != "about:blank":
            self._check_url(self.page.url)
        await self._invalidate()
        try:
            snapshotter = await self._ensure_cdp()
            if snapshotter is not None:
                return await self._cdp_snapshot(snapshotter)
        except Exception:
            # Browsers may deny individual protocol domains. The semantic JS
            # path remains a complete degradation mode, not a screenshot path.
            pass
        return await self._legacy_snapshot()

    async def snapshot(self) -> BrowserSnapshot:
        async with self._lock:
            if (self._successor_ready and self._snapshot is not None
                    and time.monotonic() - self._observed_at <= self.snapshot_ttl
                    and self.page.url == self._snapshot.url):
                self._successor_ready = False
                return self._snapshot
            return await self._observe_locked()

    async def _current_snapshot(self, snapshot_id):
        if (not self._snapshot or snapshot_id != self._snapshot.snapshot_id
                or time.monotonic() - self._observed_at > self.snapshot_ttl
                or self.page.url != self._snapshot.url):
            raise ValueError("snapshot expired or page changed; call browser_snapshot again")
        self._check_url(self.page.url)
        return self._snapshot

    async def _target(self, snapshot_id, index):
        await self._current_snapshot(snapshot_id)
        target = self._targets.get(index)
        if not target:
            raise ValueError("target changed; call browser_snapshot again")
        if target.backend_node_id is not None:
            await self._cdp_preflight(target)
            return target
        if _signature(await target.handle.evaluate(_STATE_JS)) != target.signature:
            raise ValueError("target changed; call browser_snapshot again")
        if not await target.handle.evaluate(_REACHABLE_JS):
            raise ValueError("target is covered, disabled, or detached; inspect again")
        return target

    async def _cdp_object(self, target: Target) -> str:
        result = await self._snapshotter.send("DOM.resolveNode", {"backendNodeId": target.backend_node_id})
        object_id = result.get("object", {}).get("objectId")
        if not object_id:
            raise ValueError("target detached; call browser_snapshot again")
        target.object_id = object_id
        return object_id

    async def _cdp_call(self, target: Target, declaration: str, *, arguments=None, return_by_value=True):
        object_id = await self._cdp_object(target)
        result = await self._snapshotter.send("Runtime.callFunctionOn", {
            "objectId": object_id, "functionDeclaration": declaration,
            "returnByValue": return_by_value, "awaitPromise": True,
            "arguments": [{"value": value} for value in (arguments or [])],
        })
        if result.get("exceptionDetails"):
            raise ValueError("target detached; call browser_snapshot again")
        return result.get("result", {}).get("value")

    async def _cdp_preflight(self, target: Target):
        rec = target.record
        state = await self._cdp_call(target, """function(){
          if (!this.isConnected || this.disabled || this.matches(':disabled') || this.closest('[inert]') || this.getAttribute('aria-disabled') === 'true') return null;
          const r=this.getBoundingClientRect(), x=r.left+r.width/2, y=r.top+r.height/2;
          const hit=this.ownerDocument.elementFromPoint(x,y);
          const root=this.getRootNode(), host=root&&root.host;
          return {tag:this.tagName.toLowerCase(),type:(this.getAttribute('type')||'').toLowerCase(),
            value:this.isContentEditable?this.textContent:('value' in this?this.value:null),checked:'checked' in this?!!this.checked:null,
            x:r.x,y:r.y,w:r.width,h:r.height,reachable:!!hit&&(hit===this||this.contains(hit)||hit===host||(host&&host.contains(hit))),url:this.ownerDocument.location.href};
        }""")
        if (not state or not state.get("reachable")
                or (rec.get("document_url") and state.get("url") != rec.get("document_url"))
                or self.page.url != self._snapshot.url):
            raise ValueError("target changed, covered, or page guard changed; call browser_snapshot again")
        if state.get("tag", "").upper() != rec.get("node_name", "").upper():
            raise ValueError("target semantics changed; call browser_snapshot again")
        expected = dict(rec)
        scroll_x = 0 if rec.get("root_document") else float(rec.get("root_scroll_x", 0))
        scroll_y = 0 if rec.get("root_document") else float(rec.get("root_scroll_y", 0))
        expected["x"] = float(rec.get("x", 0)) - float(rec.get("frame_offset_x", 0)) + scroll_x
        expected["y"] = float(rec.get("y", 0)) - float(rec.get("frame_offset_y", 0)) + scroll_y
        for key in ("x", "y", "w", "h"):
            if abs(float(state.get(key, 0)) - float(expected.get(key, 0))) > 2:
                raise ValueError("target geometry changed; call browser_snapshot again")
        if (rec.get("node_name", "").upper() != "SELECT" and rec.get("input_type") != "password"
                and rec.get("value") is not None and state.get("value") != rec.get("value")):
            raise ValueError("target value changed; call browser_snapshot again")
        if rec.get("checked") is not None and bool(state.get("checked")) != bool(rec.get("checked")):
            raise ValueError("target state changed; call browser_snapshot again")
        return state

    async def _cdp_click(self, target: Target):
        x, y = float(target.record["cx"]), float(target.record["cy"])
        await self._snapshotter.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y,
                                                                   "button": "left", "clickCount": 1})
        await self._snapshotter.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y,
                                                                   "button": "left", "clickCount": 1})

    async def _successor(self) -> BrowserSnapshot | None:
        try:
            successor = await self._observe_locked()
            self._successor_ready = True
            return successor
        except Exception:
            await self._invalidate()
            return None

    def _allow(self, action, target, text=""):
        if not self.allow_writes:
            return False
        rec = target.record
        box = BoundingBox(x=round(rec["x"]), y=round(rec["y"]), w=round(rec["w"]), h=round(rec["h"]))
        element = ScreenElement(id=rec["index"], label=rec["name"], role=rec["role"],
                                bbox=box, cx=round(rec["cx"]), cy=round(rec["cy"]))
        page_url = self.page.url if self.page else ""
        policy = classify(action, element=element, text=text, url=page_url)
        return self.governor.allow(policy, f'{action}: {rec["role"]} {rec["name"]}\nSite: {origin(page_url)}')

    async def click(self, snapshot_id: str, index: int) -> Receipt:
        async with self._lock:
            executed = False
            try:
                target = await self._target(snapshot_id, index)
                # Form submission is semantic, even when the label is "Next".
                submits = (await self._cdp_call(target,
                    "function(){return !!this.form && ['submit','image'].includes(this.type)}")
                    if isinstance(target.handle, _CDPHandle) else
                    await target.handle.evaluate("el => !!el.form && ['submit','image'].includes(el.type)"))
                allowed = self.allow_writes and (
                    self.governor.allow(Policy.RESTRICTED_ACTION,
                                        f'Submit this form: {target.record["name"]}\nSite: {origin(self.page.url)}')
                    if submits else self._allow("click_element", target)
                )
                if not allowed:
                    return Receipt(status="blocked", action="click", message="Write policy or confirmation denied the action.")
                # Approval can take time: revalidate the exact same target.
                await self._target(snapshot_id, index)
                executed = None  # a timeout may happen after input was dispatched
                if isinstance(target.handle, _CDPHandle):
                    await self._cdp_click(target)
                else:
                    await target.handle.click(timeout=5000)
                executed = True
                successor = await self._successor()
                return Receipt(status="unverified", action="click", executed=True,
                    message="Click dispatched. Check an explicit postcondition before claiming completion.",
                    evidence={"target": target.record["name"], "url": self.page.url,
                              "execution_path": "dom", "background": True,
                              "successor_snapshot_id": successor.snapshot_id if successor else None})
            except ValueError as e:
                return Receipt(status="stale", action="click", message=redact(str(e)))
            except Exception as e:
                return Receipt(status="error", action="click", executed=executed, message=redact(str(e)))
            finally:
                if not self._successor_ready:
                    await self._invalidate()

    async def select(self, snapshot_id: str, index: int, value: str) -> Receipt:
        async with self._lock:
            executed = False
            try:
                target = await self._target(snapshot_id, index)
                tag = (await self._cdp_call(target, "function(){return this.tagName.toLowerCase()}")
                       if target.backend_node_id is not None else
                       await target.handle.evaluate("el => el.tagName.toLowerCase()"))
                if tag != "select":
                    raise TypeError("target is not a select control")
                if not self._allow("select_option", target, value):
                    return Receipt(status="blocked", action="select", message="Write policy or confirmation denied the action.")
                await self._target(snapshot_id, index)
                executed = None
                if target.backend_node_id is not None:
                    actual = await self._cdp_call(target, """function(value){
                      const option=Array.from(this.options||[]).find(o=>o.value===value);
                      if(!option)return null; this.value=value;
                      this.dispatchEvent(new Event('input',{bubbles:true}));
                      this.dispatchEvent(new Event('change',{bubbles:true})); return this.value;
                    }""", arguments=[value])
                else:
                    await target.handle.select_option(value=value, timeout=5000)
                    actual = await target.handle.evaluate("el => el.value")
                executed = True
                matches = actual == value
                successor = await self._successor()
                return Receipt(status="verified" if matches else "unverified", action="select", executed=True,
                    message="Selected value read back." if matches else "Control did not retain the selected value.",
                    evidence={"value_matches": matches, "selected_value": actual,
                              "execution_path": "dom", "background": True,
                              "successor_snapshot_id": successor.snapshot_id if successor else None})
            except ValueError as e:
                return Receipt(status="stale", action="select", message=redact(str(e)))
            except Exception as e:
                return Receipt(status="error", action="select", executed=executed, message=redact(str(e)))
            finally:
                if not self._successor_ready:
                    await self._invalidate()

    async def set_checked(self, snapshot_id: str, index: int, checked: bool) -> Receipt:
        async with self._lock:
            executed = False
            try:
                target = await self._target(snapshot_id, index)
                input_type = (await self._cdp_call(target, "function(){return (this.type||'').toLowerCase()}")
                              if target.backend_node_id is not None else
                              await target.handle.evaluate("el => (el.type || '').toLowerCase()"))
                if input_type not in {"checkbox", "radio"}:
                    raise TypeError("target is not a checkbox or radio control")
                if not self._allow("set_checked", target):
                    return Receipt(status="blocked", action="set_checked", message="Write policy or confirmation denied the action.")
                await self._target(snapshot_id, index)
                executed = None
                if target.backend_node_id is not None:
                    current = bool(await self._cdp_call(target, "function(){return !!this.checked}"))
                    if current is not checked:
                        await self._cdp_click(target)
                    actual = bool(await self._cdp_call(target, "function(){return !!this.checked}"))
                else:
                    await target.handle.set_checked(checked, timeout=5000)
                    actual = bool(await target.handle.evaluate("el => el.checked"))
                executed = True
                matches = actual is checked
                successor = await self._successor()
                return Receipt(status="verified" if matches else "unverified", action="set_checked", executed=True,
                    message="Checked state read back." if matches else "Control did not retain the requested checked state.",
                    evidence={"checked_matches": matches, "checked": actual,
                              "execution_path": "dom", "background": True,
                              "successor_snapshot_id": successor.snapshot_id if successor else None})
            except ValueError as e:
                return Receipt(status="stale", action="set_checked", message=redact(str(e)))
            except Exception as e:
                return Receipt(status="error", action="set_checked", executed=executed, message=redact(str(e)))
            finally:
                if not self._successor_ready:
                    await self._invalidate()

    async def upload(self, snapshot_id: str, index: int, path: str) -> Receipt:
        async with self._lock:
            executed = False
            try:
                target = await self._target(snapshot_id, index)
                input_type = (await self._cdp_call(target, "function(){return (this.type||'').toLowerCase()}")
                              if target.backend_node_id is not None else
                              await target.handle.evaluate("el => (el.type || '').toLowerCase()"))
                if input_type != "file":
                    raise TypeError("target is not a file input")
                requested = Path(path).expanduser()
                summary = f"Upload {requested.name or 'local file'} to {target.record['name'] or 'file input'}"
                if not self.allow_writes or not self.governor.allow(Policy.RESTRICTED_ACTION, summary):
                    return Receipt(status="blocked", action="upload", message="Write policy or confirmation denied the action.")
                try:
                    file = requested.resolve(strict=True)
                    if not file.is_file():
                        raise OSError
                    if file.stat().st_size > 10 * 1024 * 1024:
                        return Receipt(status="error", action="upload", message="upload file exceeds 10 MiB")
                except OSError:
                    return Receipt(status="error", action="upload", message="upload file is unavailable")
                await self._target(snapshot_id, index)
                executed = None
                if target.backend_node_id is not None:
                    await self._snapshotter.send("DOM.setFileInputFiles", {
                        "backendNodeId": target.backend_node_id, "files": [str(file)]})
                    uploaded = await self._cdp_call(target,
                        "function(){return Array.from(this.files||[]).map(f=>({name:f.name,size:f.size}))}")
                else:
                    await target.handle.set_input_files(str(file), timeout=5000)
                    uploaded = await target.handle.evaluate(
                        "el => Array.from(el.files || []).map(f => ({name: f.name, size: f.size}))")
                executed = True
                matches = len(uploaded) == 1 and uploaded[0]["name"] == file.name
                successor = await self._successor()
                return Receipt(status="verified" if matches else "unverified", action="upload", executed=True,
                    message="Selected file read back." if matches else "File input did not retain the selected file.",
                    evidence={"file_matches": matches, "file_name": file.name,
                              "execution_path": "dom", "background": True,
                              "bytes": uploaded[0]["size"] if uploaded else None,
                              "successor_snapshot_id": successor.snapshot_id if successor else None})
            except ValueError as e:
                return Receipt(status="stale", action="upload", executed=executed, message=redact(str(e)))
            except Exception as e:
                return Receipt(status="error", action="upload", executed=executed, message=redact(str(e)))
            finally:
                if not self._successor_ready:
                    await self._invalidate()

    async def scroll(self, snapshot_id: str, delta_y: int) -> Receipt:
        async with self._lock:
            executed = False
            if not isinstance(delta_y, int) or isinstance(delta_y, bool) or delta_y == 0 or abs(delta_y) > 10000:
                return Receipt(status="error", action="scroll",
                               message="delta_y must be a non-zero integer between -10000 and 10000")
            try:
                await self._current_snapshot(snapshot_id)
                executed = None
                position = await self.page.evaluate("""delta => {
                    const before = Math.round(window.scrollY || 0);
                    window.scrollBy(0, delta);
                    return {before, after: Math.round(window.scrollY || 0)};
                }""", delta_y)
                executed = True
                changed = position["before"] != position["after"]
                return Receipt(status="verified" if changed else "unverified", action="scroll", executed=True,
                    message="Scroll position changed." if changed else "Scroll reached a page boundary.",
                    evidence={"before_y": position["before"], "after_y": position["after"],
                              "execution_path": "dom", "background": True,
                              "requested_delta_y": delta_y})
            except ValueError as e:
                return Receipt(status="stale", action="scroll", executed=executed, message=redact(str(e)))
            except Exception as e:
                return Receipt(status="error", action="scroll", executed=executed, message=redact(str(e)))
            finally:
                await self._invalidate()

    async def fill(self, snapshot_id: str, index: int, text: str) -> Receipt:
        async with self._lock:
            executed = False
            try:
                if len(text) > 100000:
                    raise ValueError("text exceeds 100000 characters")
                target = await self._target(snapshot_id, index)
                password = ((target.record.get("input_type") or "").lower() == "password"
                            if target.backend_node_id is not None else
                            await target.handle.get_attribute("type") == "password")
                if not self._allow("type_text", target, text) or (password and not self.governor.allow(
                        Policy.RESTRICTED_ACTION, "Fill password field")):
                    return Receipt(status="blocked", action="fill", message="Write policy or confirmation denied the action.")
                await self._target(snapshot_id, index)
                executed = None
                if target.backend_node_id is not None:
                    if (target.record.get("input_type") or "").lower() in {
                            "date", "time", "month", "week", "datetime-local", "color", "range"}:
                        await self._cdp_call(target, """function(value){
                          this.focus(); this.value=value;
                          this.dispatchEvent(new Event('input',{bubbles:true}));
                          this.dispatchEvent(new Event('change',{bubbles:true})); return this.value;
                        }""", arguments=[text])
                    else:
                        await self._cdp_call(target, """function(){
                          this.focus();
                          if(typeof this.select==='function')this.select();
                          else {const s=this.ownerDocument.getSelection();const r=this.ownerDocument.createRange();r.selectNodeContents(this);s.removeAllRanges();s.addRange(r)}
                          return true;
                        }""")
                        await self._snapshotter.send("Input.insertText", {"text": text})
                    actual = await self._cdp_call(target,
                        "function(){return this.isContentEditable?this.textContent:this.value}")
                else:
                    await target.handle.fill(text, timeout=5000)
                    actual = await target.handle.evaluate("el => el.isContentEditable ? el.textContent : el.value")
                executed = True
                matches = actual == text
                successor = await self._successor()
                return Receipt(status="verified" if matches else "unverified", action="fill", executed=True,
                    message="Field value read back." if matches else "Field did not retain the expected value.",
                    evidence={"value_matches": matches, "characters": len(text),
                              "execution_path": "dom", "background": True,
                              "successor_snapshot_id": successor.snapshot_id if successor else None})
            except ValueError as e:
                return Receipt(status="stale", action="fill", message=redact(str(e)))
            except Exception as e:
                return Receipt(status="error", action="fill", executed=executed, message=redact(str(e)))
            finally:
                if not self._successor_ready:
                    await self._invalidate()

    async def verify_text(self, text: str) -> Receipt:
        async with self._lock:
            try:
                if not text.strip():
                    raise ValueError("verification text must be non-empty")
                await self._ensure()
                self._check_url(self.page.url)
                found = text in await self.page.locator("body").inner_text(timeout=5000)
                return Receipt(status="verified" if found else "unverified", action="verify_text",
                    message="Text observed on the page." if found else "Expected text was not observed.",
                    evidence={"predicate": "visible_text_contains", "matched": found, "url": self.page.url})
            except Exception as e:
                return Receipt(status="error", action="verify_text", message=redact(str(e)))

    async def act(self, action: Literal["click", "fill"], name: str, role: str = "", text: str = "") -> Receipt:
        """Refresh and resolve an exact unique name, then use the normal freshness gate."""
        if action not in {"click", "fill"} or not name.strip():
            return Receipt(status="error", action=str(action), message="Choose click or fill and a non-empty exact control name.")
        try:
            snap = await self.snapshot()
            matches = [e for e in snap.elements if e["name"] == name and (not role or e["role"] == role)]
            if len(matches) != 1:
                return Receipt(status="blocked", action=action,
                    message=f"Expected one exact target; found {len(matches)}. Inspect and use snapshot_id/index to disambiguate.",
                    evidence={"matches": len(matches)})
            index = matches[0]["index"]
            # The existing gate catches concurrent observations or page changes.
            if action == "fill":
                return await self.fill(snap.snapshot_id, index, text)
            return await self.click(snap.snapshot_id, index)
        except Exception as e:
            return Receipt(status="error", action=action, message=redact(str(e)))

    async def form_state(self):
        from phase2_mcp.page_snapshot import FORM_STATE_JS
        async with self._lock:
            await self._ensure()
            self._check_url(self.page.url)
            return await self.page.evaluate(FORM_STATE_JS)

    async def highlight(self, snapshot_id: str, index: int, label: str = "Next step", duration: int = 8000) -> bool:
        from mcp_vision.guidance import overlay_function
        async with self._lock:
            target = await self._target(snapshot_id, index)
            if target.backend_node_id is not None:
                return bool(await self._cdp_call(target, overlay_function(), arguments=[label, int(duration)]))
            return bool(await target.handle.evaluate(
                f"(el, args) => ({overlay_function()}).call(el, args[0], args[1])", [label, int(duration)]))

    async def clear_highlight(self):
        if self.page:
            await self.page.evaluate("window.__mcpVisionHighlight?.()")

    async def screenshot(self) -> bytes:
        async with self._lock:
            await self._ensure()
            if self.page.url != "about:blank":
                self._check_url(self.page.url)
            return await self.page.screenshot(type="png")

    async def settle(self, operation: str = "") -> None:
        """Compatibility no-op; semantic predicate waits own readiness timing."""
        return None

    async def act_batch(self, snapshot_id: str, actions: list[dict]) -> list[Receipt]:
        """Run a bounded local batch, stopping at the first state guard change.

        Each action consumes the predecessor observation and produces a cached
        successor.  Callers never carry a raw index across that boundary.
        """
        if len(actions) > 8:
            raise ValueError("local action batches are limited to eight actions")
        receipts = []
        current_id = snapshot_id
        current = await self._current_snapshot(current_id)
        guard = dict(current.guard)
        for spec in actions:
            action = spec.get("action")
            identity = spec.get("identity")
            matches = [rec for rec in current.elements if rec.get("identity") == identity]
            if len(matches) != 1:
                receipts.append(Receipt(status="stale", action=str(action),
                    message="target identity is absent or ambiguous in the current observation"))
                break
            index = matches[0]["index"]
            if action == "click":
                receipt = await self.click(current_id, index)
            elif action == "fill":
                receipt = await self.fill(current_id, index, str(spec.get("text", "")))
            else:
                receipts.append(Receipt(status="error", action=str(action),
                    message="batch actions support click and fill"))
                break
            receipts.append(receipt)
            successor_id = receipt.evidence.get("successor_snapshot_id")
            if not successor_id or not self._snapshot or self._snapshot.snapshot_id != successor_id:
                break
            current = self._snapshot
            current_id = successor_id
            new_guard = dict(current.guard)
            if any(guard.get(key) != new_guard.get(key) for key in ("url", "root", "focus")):
                break
            guard = new_guard
        return receipts

    async def close(self):
        async with self._lock:
            await self._invalidate()
            await self._detach_cdp()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
            self.page = self._browser = self._context = self._playwright = None

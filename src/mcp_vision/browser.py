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
    url: str
    title: str
    text: str
    elements: list[dict]
    pruned: dict = Field(default_factory=dict)
    source: str = "dom-accessibility"


@dataclass
class Target:
    handle: object
    signature: str
    record: dict


_STATE_JS = r"""el => ({
  connected: el.isConnected && el.ownerDocument === document,
  html: el.outerHTML,
  tag: el.tagName.toLowerCase(), type: (el.type || '').toLowerCase(),
  value: el.value, checked: el.checked, selectedIndex: el.selectedIndex,
  options: el.options ? Array.from(el.options).map(o => ({
    value: o.value, label: o.textContent.trim(), disabled: o.disabled
  })) : [],
  labels: el.labels ? Array.from(el.labels).map(n => n.textContent) : [],
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
                 governor=None, headless=True, snapshot_ttl=30):
        self.page = page
        self.allow_writes = allow_writes
        self.allowed_origins = frozenset(origin(x) for x in allowed_origins)
        self.governor = governor or Governor()
        self.headless = headless
        self.snapshot_ttl = snapshot_ttl
        self._playwright = self._browser = self._context = None
        self._snapshot = None
        self._targets = {}
        self._observed_at = 0.0
        self._lock = asyncio.Lock()

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
            self._browser = await self._playwright.chromium.launch(headless=self.headless)
            self._context = await self._browser.new_context(accept_downloads=False, service_workers="block")
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
                               evidence={"url": self.page.url})
            except Exception as e:
                return Receipt(status="error", action="navigate", executed=executed, message=redact(str(e)))

    async def snapshot(self) -> BrowserSnapshot:
        async with self._lock:
            await self._ensure()
            if self.page.url != "about:blank":
                self._check_url(self.page.url)
            await self._invalidate()
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
                self._targets[rec["index"]] = Target(handle, _signature(state), rec)
                records.append(rec)
            text = await self.page.locator("body").inner_text(timeout=5000)
            self._snapshot = BrowserSnapshot(snapshot_id=uuid.uuid4().hex,
                url=self.page.url, title=await self.page.title(), text=text[:12000],
                elements=records, pruned=raw.get("pruned", {}))
            self._observed_at = time.monotonic()
            return self._snapshot

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
        if not target or _signature(await target.handle.evaluate(_STATE_JS)) != target.signature:
            raise ValueError("target changed; call browser_snapshot again")
        if not await target.handle.evaluate(_REACHABLE_JS):
            raise ValueError("target is covered, disabled, or detached; inspect again")
        return target

    def _allow(self, action, target, text=""):
        if not self.allow_writes:
            return False
        rec = target.record
        box = BoundingBox(x=rec["x"], y=rec["y"], w=rec["w"], h=rec["h"])
        element = ScreenElement(id=rec["index"], label=rec["name"], role=rec["role"],
                                bbox=box, cx=rec["cx"], cy=rec["cy"])
        policy = classify(action, element=element, text=text)
        return self.governor.allow(policy, f'{action}: {rec["role"]} {rec["name"]}')

    async def click(self, snapshot_id: str, index: int) -> Receipt:
        async with self._lock:
            executed = False
            try:
                target = await self._target(snapshot_id, index)
                # Form submission is semantic, even when the label is "Next".
                submits = await target.handle.evaluate("el => !!el.form && ['submit','image'].includes(el.type)")
                allowed = self.allow_writes and (
                    self.governor.allow(Policy.RESTRICTED_ACTION, "Submit this form")
                    if submits else self._allow("click_element", target)
                )
                if not allowed:
                    return Receipt(status="blocked", action="click", message="Write policy or confirmation denied the action.")
                # Approval can take time: revalidate the exact same target.
                await self._target(snapshot_id, index)
                executed = None  # a timeout may happen after input was dispatched
                await target.handle.click(timeout=5000)
                executed = True
                return Receipt(status="unverified", action="click", executed=True,
                    message="Click dispatched. Check an explicit postcondition before claiming completion.",
                    evidence={"target": target.record["name"], "url": self.page.url})
            except ValueError as e:
                return Receipt(status="stale", action="click", message=redact(str(e)))
            except Exception as e:
                return Receipt(status="error", action="click", executed=executed, message=redact(str(e)))
            finally:
                await self._invalidate()

    async def select(self, snapshot_id: str, index: int, value: str) -> Receipt:
        async with self._lock:
            executed = False
            try:
                target = await self._target(snapshot_id, index)
                if await target.handle.evaluate("el => el.tagName.toLowerCase()") != "select":
                    raise TypeError("target is not a select control")
                if not self._allow("select_option", target, value):
                    return Receipt(status="blocked", action="select", message="Write policy or confirmation denied the action.")
                await self._target(snapshot_id, index)
                executed = None
                await target.handle.select_option(value=value, timeout=5000)
                executed = True
                actual = await target.handle.evaluate("el => el.value")
                matches = actual == value
                return Receipt(status="verified" if matches else "unverified", action="select", executed=True,
                    message="Selected value read back." if matches else "Control did not retain the selected value.",
                    evidence={"value_matches": matches, "selected_value": actual})
            except ValueError as e:
                return Receipt(status="stale", action="select", message=redact(str(e)))
            except Exception as e:
                return Receipt(status="error", action="select", executed=executed, message=redact(str(e)))
            finally:
                await self._invalidate()

    async def set_checked(self, snapshot_id: str, index: int, checked: bool) -> Receipt:
        async with self._lock:
            executed = False
            try:
                target = await self._target(snapshot_id, index)
                input_type = await target.handle.evaluate("el => (el.type || '').toLowerCase()")
                if input_type not in {"checkbox", "radio"}:
                    raise TypeError("target is not a checkbox or radio control")
                if not self._allow("set_checked", target):
                    return Receipt(status="blocked", action="set_checked", message="Write policy or confirmation denied the action.")
                await self._target(snapshot_id, index)
                executed = None
                await target.handle.set_checked(checked, timeout=5000)
                executed = True
                actual = bool(await target.handle.evaluate("el => el.checked"))
                matches = actual is checked
                return Receipt(status="verified" if matches else "unverified", action="set_checked", executed=True,
                    message="Checked state read back." if matches else "Control did not retain the requested checked state.",
                    evidence={"checked_matches": matches, "checked": actual})
            except ValueError as e:
                return Receipt(status="stale", action="set_checked", message=redact(str(e)))
            except Exception as e:
                return Receipt(status="error", action="set_checked", executed=executed, message=redact(str(e)))
            finally:
                await self._invalidate()

    async def upload(self, snapshot_id: str, index: int, path: str) -> Receipt:
        async with self._lock:
            executed = False
            try:
                target = await self._target(snapshot_id, index)
                input_type = await target.handle.evaluate("el => (el.type || '').toLowerCase()")
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
                await target.handle.set_input_files(str(file), timeout=5000)
                executed = True
                uploaded = await target.handle.evaluate(
                    "el => Array.from(el.files || []).map(f => ({name: f.name, size: f.size}))")
                matches = len(uploaded) == 1 and uploaded[0]["name"] == file.name
                return Receipt(status="verified" if matches else "unverified", action="upload", executed=True,
                    message="Selected file read back." if matches else "File input did not retain the selected file.",
                    evidence={"file_matches": matches, "file_name": file.name,
                              "bytes": uploaded[0]["size"] if uploaded else None})
            except ValueError as e:
                return Receipt(status="stale", action="upload", executed=executed, message=redact(str(e)))
            except Exception as e:
                return Receipt(status="error", action="upload", executed=executed, message=redact(str(e)))
            finally:
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
                password = await target.handle.get_attribute("type") == "password"
                if not self._allow("type_text", target, text) or (password and not self.governor.allow(
                        Policy.RESTRICTED_ACTION, "Fill password field")):
                    return Receipt(status="blocked", action="fill", message="Write policy or confirmation denied the action.")
                await self._target(snapshot_id, index)
                executed = None
                await target.handle.fill(text, timeout=5000)
                executed = True
                actual = await target.handle.evaluate("el => el.isContentEditable ? el.textContent : el.value")
                matches = actual == text
                return Receipt(status="verified" if matches else "unverified", action="fill", executed=True,
                    message="Field value read back." if matches else "Field did not retain the expected value.",
                    evidence={"value_matches": matches, "characters": len(text)})
            except ValueError as e:
                return Receipt(status="stale", action="fill", message=redact(str(e)))
            except Exception as e:
                return Receipt(status="error", action="fill", executed=executed, message=redact(str(e)))
            finally:
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

    async def screenshot(self) -> bytes:
        async with self._lock:
            await self._ensure()
            if self.page.url != "about:blank":
                self._check_url(self.page.url)
            return await self.page.screenshot(type="png")

    async def close(self):
        async with self._lock:
            await self._invalidate()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
            self.page = self._browser = self._context = self._playwright = None

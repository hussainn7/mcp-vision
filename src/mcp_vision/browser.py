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
  value: el.value, checked: el.checked, selectedIndex: el.selectedIndex,
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
                self._targets[rec["index"]] = Target(handle, _signature(state), rec)
                records.append(rec)
            text = await self.page.locator("body").inner_text(timeout=5000)
            self._snapshot = BrowserSnapshot(snapshot_id=uuid.uuid4().hex,
                url=self.page.url, title=await self.page.title(), text=text[:12000],
                elements=records, pruned=raw.get("pruned", {}))
            self._observed_at = time.monotonic()
            return self._snapshot

    async def _target(self, snapshot_id, index):
        if (not self._snapshot or snapshot_id != self._snapshot.snapshot_id
                or time.monotonic() - self._observed_at > self.snapshot_ttl
                or self.page.url != self._snapshot.url):
            raise ValueError("snapshot expired or page changed; call browser_snapshot again")
        self._check_url(self.page.url)
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
                if not self._allow("click_element", target) or (submits and not self.governor.allow(
                        Policy.RESTRICTED_ACTION, "Submit this form")):
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

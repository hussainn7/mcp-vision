"""Drive existing Chrome without CDP — no automation banner."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlsplit

from mcp_vision.browser import BrowserRuntime, BrowserSnapshot, Receipt, origin
from mcp_vision.core.governor import classify
from mcp_vision.core.models import BoundingBox, Policy, ScreenElement
from mcp_vision.redaction import redact
from phase2_mcp.auth_detector import detect_auth_challenge
from phase2_mcp.chrome_native import (
    _osa_activate,
    _osa_goto,
    _osa_js,
    _osa_new_tab,
    _osa_tabs,
    ensure_chrome,
)
from phase2_mcp.page_snapshot import SNAPSHOT_JS


@dataclass
class _Tab:
    window: int
    tab: int
    url: str
    title: str
    key: str


class _PageRef:
    """Stand-in so shared helpers can read .url without Playwright."""

    def __init__(self, runtime: "NativeBrowserRuntime"):
        self._runtime = runtime

    @property
    def url(self) -> str:
        cur = self._runtime._current
        return cur.url if cur else "about:blank"

    def is_closed(self) -> bool:
        return self._runtime._current is None


@dataclass
class _Target:
    signature: str
    record: dict


def _sig(rec: dict) -> str:
    blob = {k: rec.get(k) for k in ("index", "role", "name", "x", "y", "w", "h")}
    return hashlib.sha256(json.dumps(blob, sort_keys=True).encode()).hexdigest()


def _same_url(left: str, right: str) -> bool:
    """Strict tab binding with only a trailing-root-slash tolerance.

    Substring matching can bind an agent to the wrong tab when the expected URL
    appears inside a query parameter on an unrelated page.
    """
    try:
        a, b = urlsplit(left), urlsplit(right)
        return (
            a.scheme.lower(), a.netloc.lower(), a.path.rstrip("/") or "/", a.query, a.fragment
        ) == (
            b.scheme.lower(), b.netloc.lower(), b.path.rstrip("/") or "/", b.query, b.fragment
        )
    except Exception:
        return left.rstrip("/") == right.rstrip("/")


def _tab_locator(value: str) -> tuple[int, int] | None:
    try:
        window, tab = (value or "").strip().split("|||", 1)
        return int(window), int(tab)
    except (TypeError, ValueError):
        return None


class NativeBrowserRuntime(BrowserRuntime):
    """AppleScript/JS path. Same MCP surface as live CDP, without DevTools attach."""

    def __init__(self, *, pause_for_challenges=True, **options):
        self.pause_for_challenges = pause_for_challenges
        super().__init__(**options)
        self._tabs: dict[str, _Tab] = {}
        self._current: _Tab | None = None
        self._native_targets: dict[int, _Target] = {}
        self.page = None  # keep attribute for shared helpers that check it

    async def _run(self, fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    def _eval(self, js: str) -> str:
        out = _osa_js(js)
        if isinstance(out, str) and (out.startswith("ERROR:") or out.startswith("error:")):
            raise RuntimeError(out)
        return out or ""

    async def _ensure(self):
        ensure_chrome()
        if self._current is None:
            raise RuntimeError(
                "No open tab selected. Call browser_tabs, then browser_use_tab "
                "with its tab_id and URL, or browser_open_tab for a new tab."
            )
        await self._run(_osa_activate, self._current.window, self._current.tab)

    async def tabs(self) -> dict:
        async with self._lock:
            try:
                await self._run(ensure_chrome)
                raw = await self._run(_osa_tabs)
                self._tabs = {}
                result = []
                for row in raw:
                    try:
                        self._check_url(row["url"])
                    except Exception:
                        continue
                    key = uuid.uuid4().hex[:12]
                    tab = _Tab(window=row["window"], tab=row["tab"], url=row["url"],
                               title=row["title"], key=key)
                    self._tabs[key] = tab
                    selected = (self._current is not None
                                and self._current.window == tab.window
                                and self._current.tab == tab.tab
                                and self._current.url == tab.url)
                    result.append({"tab_id": key, "url": tab.url, "title": tab.title,
                                   "selected": selected})
                return {"connected": True, "mode": "existing-chrome-native", "tabs": result,
                        "driver": "native"}
            except Exception as exc:
                return {"connected": False, "mode": "existing-chrome-native", "tabs": [],
                        "driver": "native", "error": redact(str(exc))}

    async def use_tab(self, tab_id: str, expected_url: str) -> Receipt:
        async with self._lock:
            try:
                target = self._tabs.get(tab_id)
                if target is None or target.url != expected_url:
                    return Receipt(status="stale", action="use_tab",
                                   message="Tab closed or URL changed. List tabs again.")
                here = await self._run(_osa_activate, target.window, target.tab)
                # Confirm the front tab actually matches — indices can shift after new tabs.
                if not _same_url(expected_url, here or ""):
                    return Receipt(status="stale", action="use_tab",
                                   message="Tab moved, closed, or changed URL. List tabs again.",
                                   evidence={"expected_url": expected_url, "observed_url": here or ""})
                target.url = here or target.url
                self._current = target
                self.page = _PageRef(self)
                self._root_id = f"chrome-native-{target.window}-{target.tab}"
                await self._invalidate()
                return Receipt(status="verified", action="use_tab", executed=True,
                               message="Selected the existing tab (native; no automation banner).",
                               evidence={"tab_id": tab_id, "url": target.url, "driver": "native"})
            except Exception as exc:
                return Receipt(status="error", action="use_tab", message=redact(str(exc)))

    async def open_tab(self, url: str) -> Receipt:
        async with self._lock:
            try:
                self._check_url(url)
                await self._run(ensure_chrome)
                locator = _tab_locator(await self._run(_osa_new_tab, url))
                if locator is None:
                    raise RuntimeError("Chrome did not return the new tab identity.")
                await asyncio.sleep(0.6)
                tabs = await self._run(_osa_tabs)
                if not tabs:
                    raise RuntimeError("Chrome has no tabs after open.")
                row = next((t for t in tabs
                            if (t["window"], t["tab"]) == locator), None)
                if row is None:
                    raise RuntimeError("New Chrome tab moved or closed before it could be bound.")
                active_url = await self._run(_osa_activate, row["window"], row["tab"])
                # The requested URL may already have redirected between the tab
                # listing and activation (Gmail does this routinely). The stable
                # window/tab locator proves which tab is active; require only an
                # observable web URL here and let the next snapshot verify origin.
                if urlsplit(active_url or "").scheme not in {"http", "https"}:
                    raise RuntimeError("New Chrome tab did not expose a web URL.")
                row["url"] = active_url or row["url"]
                key = uuid.uuid4().hex[:12]
                tab = _Tab(window=row["window"], tab=row["tab"], url=row["url"],
                           title=row["title"], key=key)
                self._tabs[key] = tab
                self._current = tab
                self.page = _PageRef(self)
                self._root_id = f"chrome-native-{tab.window}-{tab.tab}"
                await self._invalidate()
                return Receipt(status="verified", action="open_tab", executed=True,
                               message="Opened a tab in the existing Chrome profile (native).",
                               evidence={"tab_id": key, "requested_url": url,
                                         "url": tab.url, "driver": "native"})
            except Exception as exc:
                return Receipt(status="error", action="open_tab", message=redact(str(exc)))

    async def navigate(self, url: str) -> Receipt:
        async with self._lock:
            try:
                self._check_url(url)
                await self._ensure()
                await self._run(_osa_goto, url)
                await asyncio.sleep(0.8)
                await self._refresh_current_meta()
                await self._invalidate()
                challenge = await self._maybe_pause_for_challenge()
                return Receipt(status="verified", action="navigate", executed=True,
                               message="Navigated in existing Chrome."
                               + (" CAPTCHA/auth pause completed." if challenge else ""),
                               evidence={"url": self._current.url if self._current else url,
                                         "driver": "native", "challenge": challenge})
            except Exception as exc:
                return Receipt(status="error", action="navigate", message=redact(str(exc)))

    async def _refresh_current_meta(self):
        if not self._current:
            return
        tabs = await self._run(_osa_tabs)
        for t in tabs:
            if t["window"] == self._current.window and t["tab"] == self._current.tab:
                self._current.url = t["url"]
                self._current.title = t["title"]
                return

    async def _adopt_new_tab(self, before_tabs: list[dict]) -> bool:
        """Follow one tab created by the just-dispatched click."""
        before_urls = Counter(row.get("url", "") for row in before_tabs)
        for _ in range(5):
            rows = await self._run(_osa_tabs)
            remaining = before_urls.copy()
            created = []
            for row in rows:
                url = row.get("url", "")
                if remaining[url] > 0:
                    remaining[url] -= 1
                else:
                    created.append(row)
            if len(created) == 1:
                row = created[0]
                active_url = row.get("url", "")
                if urlsplit(active_url).scheme not in {"http", "https"}:
                    await asyncio.sleep(.25)
                    continue
                await self._run(_osa_activate, row["window"], row["tab"])
                tab = _Tab(window=row["window"], tab=row["tab"], url=active_url,
                           title=row.get("title", ""), key=uuid.uuid4().hex[:12])
                self._tabs[tab.key] = tab
                self._current = tab
                self.page = _PageRef(self)
                return True
            if len(rows) <= len(before_tabs):
                return False
            await asyncio.sleep(.25)
        return False

    async def _page_blob(self) -> tuple[str, str, str]:
        raw = await self._run(self._eval, "JSON.stringify({url: location.href, title: document.title, "
                            "text: (document.body && document.body.innerText || '').slice(0, 4000)})")
        data = json.loads(raw)
        return data.get("url") or "", data.get("title") or "", data.get("text") or ""

    async def _maybe_pause_for_challenge(self, elements=None) -> dict | None:
        if not self.pause_for_challenges:
            return None
        from mcp_vision.challenge import wait_for_user_challenge
        url, title, text = await self._page_blob()
        challenge = detect_auth_challenge(url=url, title=title, text=text, elements=elements)
        if challenge is None or challenge.challenge_type != "captcha":
            return None
        ok = await self._run(wait_for_user_challenge, challenge)
        await self._refresh_current_meta()
        return {"type": challenge.challenge_type, "service": challenge.service,
                "solved": ok, "url": url}

    async def snapshot(self) -> BrowserSnapshot:
        async with self._lock:
            await self._ensure()
            raw = await self._run(self._eval, f"JSON.stringify(({SNAPSHOT_JS})(80))")
            data = json.loads(raw) if isinstance(raw, str) else raw
            records = data.get("elements") or []
            self._native_targets = {int(r["index"]): _Target(_sig(r), r) for r in records if "index" in r}
            url = data.get("url") or (self._current.url if self._current else "")
            title = data.get("title") or (self._current.title if self._current else "")
            text = data.get("text") or ""
            # The pruned accessibility tree frequently drops JS-rendered page content
            # (e.g. Google Flights offers). Prefer the document's full rendered text
            # so evaluators and the summarizer can ground answers on real evidence.
            # Keep line breaks: result cards use them as semantic boundaries. Flattening
            # innerText made a visually complete flight page impossible to parse.
            try:
                full = await self._run(self._eval,
                    "(document.body && document.body.innerText || '').slice(0, 16000).trim()")
            except Exception:
                full = ""
            if len(full) > len(text):
                text = full
            if not url.startswith("http"):
                url = self._current.url if self._current and self._current.url.startswith("http") else url
            if not str(url).startswith("http"):
                raise RuntimeError(f"Tab URL is not HTTP(S): {url!r}")
            self._check_url(url)
            if self._current:
                self._current.url = url
                self._current.title = title
            self._snapshot = BrowserSnapshot(
                snapshot_id=uuid.uuid4().hex, root_id=self._root_id,
                url=url, title=title, text=text[:12000],
                elements=records, pruned=data.get("pruned") or {},
                facts=data.get("facts") or [], identity=data.get("identity") or {},
                source="dom-accessibility-native")
            self._observed_at = time.monotonic()
            challenge = detect_auth_challenge(url=url, title=title, text=text, elements=records)
            if challenge and challenge.challenge_type == "captcha":
                await self._maybe_pause_for_challenge(records)
            return self._snapshot

    def _allow_record(self, action: str, rec: dict, text: str = "") -> bool:
        if not self.allow_writes:
            return False
        box = BoundingBox(x=rec.get("x", 0), y=rec.get("y", 0), w=rec.get("w", 0), h=rec.get("h", 0))
        element = ScreenElement(id=rec["index"], label=rec.get("name") or "", role=rec.get("role") or "",
                                bbox=box, cx=rec.get("cx", 0), cy=rec.get("cy", 0))
        page_url = self._current.url if self._current else ""
        policy = classify(action, element=element, text=text, url=page_url)
        return self.governor.allow(policy, f'{action}: {rec.get("role")} {rec.get("name")}\nSite: {origin(page_url) if page_url.startswith("http") else page_url}')

    async def _fresh_target(self, snapshot_id: str, index: int) -> _Target:
        if (not self._snapshot or snapshot_id != self._snapshot.snapshot_id
                or time.monotonic() - self._observed_at > self.snapshot_ttl):
            raise ValueError("snapshot expired or page changed; call browser_snapshot again")
        target = self._native_targets.get(index)
        if not target:
            raise ValueError("target missing; call browser_snapshot again")
        rec = target.record
        # Re-check semantics, geometry, and the browser-verified click point.
        # Reactive pages can reuse the same DOM node for a different control.
        probe = await self._run(self._eval, f'''(() => {{
          const el = document.querySelector('[data-agent-index="{index}"]');
          if (!el) return null;
          const r = el.getBoundingClientRect();
          const tag = el.tagName.toLowerCase();
          const type = (el.getAttribute('type') || 'text').toLowerCase();
          const implicit = tag === 'a' ? (el.hasAttribute('href') ? 'link' : '') :
            (tag === 'button' || tag === 'summary') ? 'button' : tag === 'select' ? 'combobox' :
            tag === 'textarea' ? 'textbox' : tag === 'input' ?
              (type === 'checkbox' ? 'checkbox' : type === 'radio' ? 'radio' :
               type === 'range' ? 'slider' : ['submit','button','reset','image'].includes(type) ? 'button' : 'textbox') : '';
          const labels = el.labels ? Array.from(el.labels).map(n => n.innerText || '').join(' ') : '';
          const labelled = (el.getAttribute('aria-labelledby') || '').split(/\\s+/).filter(Boolean)
            .map(id => document.getElementById(id)?.innerText || '').join(' ');
          const name = (labelled || el.getAttribute('aria-label') || labels ||
            el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('alt') ||
            el.innerText || (['button','submit','reset'].includes(type) ? el.value : '') || '')
            .replace(/\\s+/g, ' ').trim().slice(0,100);
          const hit = document.elementFromPoint({int(rec.get('cx', 0))}, {int(rec.get('cy', 0))});
          return JSON.stringify({{index:{index}, role:(el.getAttribute('role') || implicit).toLowerCase(),
            name, x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height),
            reachable: !!hit && (hit === el || el.contains(hit))}});
        }})()''')
        if not probe or probe == "null":
            raise ValueError("target changed; call browser_snapshot again")
        live = json.loads(probe)
        if not live.pop("reachable", False) or _sig(live) != target.signature:
            raise ValueError("target changed or became covered; call browser_snapshot again")
        return target

    async def click(self, snapshot_id: str, index: int) -> Receipt:
        async with self._lock:
            executed = False
            try:
                await self._ensure()
                target = await self._fresh_target(snapshot_id, index)
                rec = target.record
                # Form submit buttons always restricted
                submits = await self._run(self._eval, f'''(() => {{
                  const el = document.querySelector('[data-agent-index="{index}"]');
                  return !!(el && el.form && ['submit','image'].includes(el.type));
                }})()''')
                is_submit = str(submits).lower() in {"true", "1"}
                allowed = self.allow_writes and (
                    self.governor.allow(Policy.RESTRICTED_ACTION,
                        f'Submit this form: {rec.get("name")}\nSite: {self._current.url if self._current else ""}')
                    if is_submit else self._allow_record("click_element", rec)
                )
                if not allowed:
                    return Receipt(status="blocked", action="click",
                                   message="Write policy or confirmation denied the action.")
                await self._fresh_target(snapshot_id, index)
                executed = None
                out = await self._run(self._eval, f'''(() => {{
                  const el = document.querySelector('[data-agent-index="{index}"]');
                  if (!el) return "missing";
                  el.click();
                  return "ok";
                }})()''')
                if out != "ok":
                    return Receipt(status="stale", action="click", message="target missing after approval")
                executed = True
                return Receipt(status="unverified", action="click", executed=True,
                               message="Click dispatched. Check an explicit postcondition before claiming completion.",
                               evidence={"target": rec.get("name"), "url": self._current.url if self._current else "",
                                         "driver": "native"})
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
                await self._ensure()
                target = await self._fresh_target(snapshot_id, index)
                rec = target.record
                password = (rec.get("input_type") == "password"
                            or "password" in (rec.get("name") or "").lower())
                if not self._allow_record("type_text", rec, text) or (
                        password and not self.governor.allow(Policy.RESTRICTED_ACTION, "Fill password field")):
                    return Receipt(status="blocked", action="fill",
                                   message="Write policy or confirmation denied the action.")
                await self._fresh_target(snapshot_id, index)
                payload = json.dumps(text)
                executed = None
                out = await self._run(self._eval, f'''(() => {{
                  const el = document.querySelector('[data-agent-index="{index}"]');
                  if (!el) return "missing";
                  el.focus();
                  if (el.isContentEditable) {{ el.textContent = {payload}; }}
                  else {{ el.value = {payload}; }}
                  el.dispatchEvent(new Event('input', {{bubbles:true}}));
                  el.dispatchEvent(new Event('change', {{bubbles:true}}));
                  return el.isContentEditable ? el.textContent : el.value;
                }})()''')
                if out == "missing":
                    return Receipt(status="stale", action="fill", message="target missing after approval")
                executed = True
                matches = out == text
                return Receipt(status="verified" if matches else "unverified", action="fill", executed=True,
                               message="Field value read back." if matches else "Field did not retain the expected value.",
                               evidence={"value_matches": matches, "characters": len(text), "driver": "native"})
            except ValueError as e:
                return Receipt(status="stale", action="fill", message=redact(str(e)))
            except Exception as e:
                return Receipt(status="error", action="fill", executed=executed, message=redact(str(e)))
            finally:
                await self._invalidate()

    async def select(self, snapshot_id: str, index: int, value: str) -> Receipt:
        async with self._lock:
            try:
                await self._ensure()
                target = await self._fresh_target(snapshot_id, index)
                if not self._allow_record("select_option", target.record, value):
                    return Receipt(status="blocked", action="select",
                                   message="Write policy or confirmation denied the action.")
                payload = json.dumps(value)
                out = await self._run(self._eval, f'''(() => {{
                  const el = document.querySelector('[data-agent-index="{index}"]');
                  if (!el || el.tagName.toLowerCase() !== 'select') return null;
                  el.value = {payload};
                  el.dispatchEvent(new Event('change', {{bubbles:true}}));
                  return el.value;
                }})()''')
                if out is None or out == "null":
                    return Receipt(status="error", action="select", message="target is not a select control")
                matches = out == value
                return Receipt(status="verified" if matches else "unverified", action="select", executed=True,
                               message="Selected value read back." if matches else "Control did not retain the selected value.",
                               evidence={"value_matches": matches, "selected_value": out, "driver": "native"})
            except ValueError as e:
                return Receipt(status="stale", action="select", message=redact(str(e)))
            except Exception as e:
                return Receipt(status="error", action="select", message=redact(str(e)))
            finally:
                await self._invalidate()

    async def set_checked(self, snapshot_id: str, index: int, checked: bool) -> Receipt:
        async with self._lock:
            try:
                await self._ensure()
                target = await self._fresh_target(snapshot_id, index)
                if not self._allow_record("set_checked", target.record):
                    return Receipt(status="blocked", action="set_checked",
                                   message="Write policy or confirmation denied the action.")
                flag = "true" if checked else "false"
                out = await self._run(self._eval, f'''(() => {{
                  const el = document.querySelector('[data-agent-index="{index}"]');
                  if (!el) return null;
                  el.checked = {flag};
                  el.dispatchEvent(new Event('change', {{bubbles:true}}));
                  return !!el.checked;
                }})()''')
                actual = str(out).lower() in {"true", "1"}
                matches = actual is checked
                return Receipt(status="verified" if matches else "unverified", action="set_checked", executed=True,
                               message="Checked state read back." if matches else "Control did not retain the requested checked state.",
                               evidence={"checked_matches": matches, "checked": actual, "driver": "native"})
            except ValueError as e:
                return Receipt(status="stale", action="set_checked", message=redact(str(e)))
            except Exception as e:
                return Receipt(status="error", action="set_checked", message=redact(str(e)))
            finally:
                await self._invalidate()

    async def upload(self, snapshot_id: str, index: int, path: str) -> Receipt:
        return Receipt(status="error", action="upload",
                       message="File upload needs --driver cdp. Native Chrome cannot set file inputs safely.")

    async def scroll(self, snapshot_id: str, delta_y: int) -> Receipt:
        async with self._lock:
            if not isinstance(delta_y, int) or isinstance(delta_y, bool) or delta_y == 0 or abs(delta_y) > 10000:
                return Receipt(status="error", action="scroll",
                               message="delta_y must be a non-zero integer between -10000 and 10000")
            try:
                if not self._snapshot or snapshot_id != self._snapshot.snapshot_id:
                    raise ValueError("snapshot expired or page changed; call browser_snapshot again")
                await self._ensure()
                out = await self._run(self._eval, f'''(() => {{
                  const before = Math.round(window.scrollY || 0);
                  window.scrollBy(0, {int(delta_y)});
                  return JSON.stringify({{before, after: Math.round(window.scrollY || 0)}});
                }})()''')
                position = json.loads(out)
                changed = position["before"] != position["after"]
                return Receipt(status="verified" if changed else "unverified", action="scroll", executed=True,
                               message="Scroll position changed." if changed else "Scroll reached a page boundary.",
                               evidence={**position, "requested_delta_y": delta_y, "driver": "native"})
            except ValueError as e:
                return Receipt(status="stale", action="scroll", message=redact(str(e)))
            except Exception as e:
                return Receipt(status="error", action="scroll", message=redact(str(e)))
            finally:
                await self._invalidate()

    async def verify_text(self, text: str) -> Receipt:
        async with self._lock:
            try:
                if not text.strip():
                    raise ValueError("verification text must be non-empty")
                await self._ensure()
                body = await self._run(self._eval, "(document.body && document.body.innerText || '')")
                found = text in body
                url = self._current.url if self._current else ""
                return Receipt(status="verified" if found else "unverified", action="verify_text",
                               message="Text observed on the page." if found else "Expected text was not observed.",
                               evidence={"predicate": "visible_text_contains", "matched": found, "url": url,
                                         "driver": "native"})
            except Exception as e:
                return Receipt(status="error", action="verify_text", message=redact(str(e)))

    async def form_state(self):
        from phase2_mcp.page_snapshot import FORM_STATE_JS
        async with self._lock:
            await self._ensure()
            raw = await self._run(self._eval, f"JSON.stringify(({FORM_STATE_JS})())")
            result = json.loads(raw)
            self._check_url(result['url'])
            return result

    async def highlight(self, snapshot_id: str, index: int, label: str = "Next step", duration: int = 8000) -> bool:
        from mcp_vision.guidance import overlay_script
        async with self._lock:
            await self._fresh_target(snapshot_id, index)
            result = await self._run(self._eval, overlay_script(index, label, duration))
            return str(result).lower() == "true"

    async def clear_highlight(self):
        await self._run(self._eval, "window.__mcpVisionHighlight?.(); true")

    async def screenshot(self) -> bytes:
        async with self._lock:
            await self._ensure()
            from mcp_vision.core.capture import capture_display
            return await self._run(lambda: capture_display(0).png)

    async def close(self):
        async with self._lock:
            await self._invalidate()
            self._tabs.clear()
            self._current = None
            self.page = None
            self._native_targets.clear()

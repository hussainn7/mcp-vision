"""Drive the user's real Chrome without CDP.

CDP sets navigator.webdriver and shows "controlled by automated test software".
Google (and others) then kill the session. This path never attaches DevTools:

  macOS  — Chrome's AppleScript dictionary (tabs + execute javascript)
  others — unpacked companion extension (chrome.tabs + scripting, not debugger)

The page sees a normal user. Cookies stay. The toolbar may show the extension;
there is no automation infobar.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from phase2_mcp.page_snapshot import SNAPSHOT_JS, format_elements
from phase2_mcp.tab_router import decide_route, match_tab

RELAY_PORT = 9230
EXT_DIR = Path(__file__).resolve().parent.parent / "chrome_relay"

_last_snap: dict = {"elements": [], "labels": {}}
_relay: Optional["Relay"] = None
_apple_events_js_tried = False
_forced_apple_events: Optional[bool] = None  # tests


def set_forced_apple_events(value: Optional[bool]) -> None:
    global _forced_apple_events
    _forced_apple_events = value


_SKIP_PREF_DIRS = {"system profile", "guest profile"}


def _js_blocked(out: str) -> bool:
    t = (out or "").lower()
    return "javascript through apple" in t


def _ask_to_enable_apple_events() -> bool:
    global _forced_apple_events
    if _forced_apple_events is not None:
        return _forced_apple_events
    msg = (
        "\n"
        + "=" * 72 + "\n"
        "[CHROME] This app cannot read pages until Chrome allows it.\n"
        "Press Enter to turn that on. Chrome will restart for a moment;\n"
        "your tabs come back. Type skip to cancel.\n"
        + "=" * 72 + "\n"
    )
    print(msg, file=sys.stderr)
    if not sys.stdin.isatty():
        return False
    try:
        ans = input(">> [Enter] enable / skip: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans not in ("skip", "n", "no", "abort", "q")


def _patch_allow_js_apple_events(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("browser", {})["allow_javascript_apple_events"] = True
    data.setdefault("account_values", {}).setdefault("browser", {})[
        "allow_javascript_apple_events"
    ] = True
    path.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def _chrome_pref_files(user_data_dir: Optional[Path] = None) -> list[Path]:
    from phase2_mcp.chrome_profiles import get_default_chrome_user_data_dir, list_profiles

    base = Path(user_data_dir) if user_data_dir else get_default_chrome_user_data_dir()
    if not base or not base.exists():
        return []
    files: list[Path] = []
    seen: set[Path] = set()
    for dir_name in list_profiles(base):
        if dir_name.lower() in _SKIP_PREF_DIRS:
            continue
        p = base / dir_name / "Preferences"
        if p.is_file():
            files.append(p)
            seen.add(p.resolve())
    for p in base.glob("*/Preferences"):
        if p.parent.name.lower() in _SKIP_PREF_DIRS:
            continue
        try:
            key = p.resolve()
        except OSError:
            continue
        if key not in seen and p.is_file():
            files.append(p)
            seen.add(key)
    return files


def _quit_chrome() -> None:
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "Google Chrome" to quit'],
            capture_output=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        pass
    for _ in range(40):
        if not chrome_running():
            time.sleep(1.0)
            return
        time.sleep(0.25)
    subprocess.run(["pkill", "-TERM", "-x", "Google Chrome"], capture_output=True)
    time.sleep(1.5)


def _wait_js_ready(seconds: float = 15) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if chrome_running():
            out = _osa(_JS_ACTIVE, "'mcpjs'")
            if "mcpjs" in (out or ""):
                return True
        time.sleep(0.4)
    return False


def enable_apple_events_js() -> bool:
    print(
        "[CHROME] Restarting Chrome so I can read the page (tabs come back)...",
        file=sys.stderr,
    )
    _quit_chrome()
    for p in _chrome_pref_files():
        try:
            _patch_allow_js_apple_events(p)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    subprocess.run(
        ["defaults", "write", "com.google.Chrome", "AllowJavascriptFromAppleEvents", "-bool", "true"],
        capture_output=True,
    )
    subprocess.run(["open", "-a", "Google Chrome"], capture_output=True)
    return _wait_js_ready()


def _osa_js(js: str) -> str:
    global _apple_events_js_tried
    out = _osa(_JS_ACTIVE, js)
    if not _js_blocked(out):
        return out
    if _apple_events_js_tried:
        return "ERROR: Chrome is still blocking page access. Quit Chrome fully and retry."
    _apple_events_js_tried = True
    if not _ask_to_enable_apple_events():
        return "ERROR: Chrome page access was skipped."
    if not enable_apple_events_js():
        return "ERROR: Chrome is still blocking page access after restart."
    return _osa(_JS_ACTIVE, js)


def _osa(script: str, *args: str) -> str:
    from mac_agent import osa
    return osa(script, *args)


def chrome_running() -> bool:
    if sys.platform == "darwin":
        return subprocess.run(["pgrep", "-x", "Google Chrome"], capture_output=True).returncode == 0
    return False


def available() -> bool:
    from config import cfg
    backend = getattr(cfg, "chrome_backend", "native")
    if backend == "cdp":
        return False
    if sys.platform == "darwin":
        return True
    r = get_relay()
    return r.connected()


def prefer() -> bool:
    from config import cfg
    if getattr(cfg, "chrome_backend", "native") == "cdp":
        return False
    if getattr(cfg, "chrome_isolated", False):
        return False
    if sys.platform == "darwin":
        return True
    return get_relay().connected()


def ensure_chrome() -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", "-a", "Google Chrome"], capture_output=True)
        return
    subprocess.run(["google-chrome"], capture_output=True)


# --- macOS AppleScript -------------------------------------------------------

_LIST_TABS = '''
tell application "Google Chrome"
    set out to ""
    set wi to 1
    repeat with w in windows
        set ti to 1
        repeat with t in tabs of w
            set out to out & wi & tab & ti & tab & (URL of t) & tab & (title of t) & linefeed
            set ti to ti + 1
        end repeat
        set wi to wi + 1
    end repeat
    return out
end tell
'''

_JS_ACTIVE = '''
tell application "Google Chrome"
    if (count of windows) is 0 then return "ERROR: no Chrome windows"
    tell active tab of front window
        return execute javascript (item 1 of argv)
    end tell
end tell
'''


def _parse_tab_lines(raw: str) -> list[dict]:
    tabs = []
    for line in (raw or "").splitlines():
        parts = line.split("\t", 3)
        if len(parts) < 4:
            continue
        try:
            w, t = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        tabs.append({"window": w, "tab": t, "url": parts[2], "title": parts[3]})
    return tabs


def _osa_tabs() -> list[dict]:
    raw = _osa(_LIST_TABS)
    if raw.startswith("error:"):
        return []
    return _parse_tab_lines(raw)


def _osa_activate(window: int, tab: int) -> str:
    return _osa(
        '''
        tell application "Google Chrome"
            set index of window (item 1 of argv as integer) to 1
            set active tab index of window 1 to (item 2 of argv as integer)
            activate
        end tell
        ''',
        str(window), str(tab),
    )


def _osa_new_tab(url: str) -> str:
    return _osa(
        '''
        tell application "Google Chrome"
            if (count of windows) is 0 then make new window
            tell window 1
                set t to make new tab with properties {URL:item 1 of argv}
                set active tab index to (index of t)
            end tell
            activate
        end tell
        ''',
        url,
    )


def _osa_goto(url: str) -> str:
    return _osa(
        '''
        tell application "Google Chrome"
            if (count of windows) is 0 then make new window
            set URL of active tab of window 1 to (item 1 of argv)
            activate
        end tell
        ''',
        url,
    )


# --- extension relay (Win/Linux, optional on Mac) ----------------------------

class Relay:
    def __init__(self):
        self._cmd = None
        self._cmd_id = 0
        self._result = None
        self._event = threading.Event()
        self._seen = 0
        self._lock = threading.Lock()
        self._httpd = None
        self._thread = None

    def connected(self) -> bool:
        return self._seen > 0

    def start(self):
        if self._thread:
            return
        handler = _make_handler(self)
        self._httpd = ThreadingHTTPServer(("127.0.0.1", RELAY_PORT), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def call(self, method: str, **params) -> dict:
        self.start()
        with self._lock:
            self._cmd_id += 1
            self._result = None
            self._event.clear()
            self._cmd = {"id": self._cmd_id, "method": method, "params": params}
        if not self._event.wait(timeout=20):
            return {"ok": False, "error": "extension did not answer (load chrome_relay unpacked)"}
        return self._result or {"ok": False, "error": "empty"}


def _make_handler(relay: Relay):
    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "content-type")
            self.end_headers()

        def do_GET(self):
            relay._seen += 1
            if self.path.startswith("/poll"):
                cmd = relay._cmd
                relay._cmd = None
                self._json(200, cmd or {})
                return
            self._json(200, {"ok": True, "waiting": relay._cmd is not None})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            try:
                data = json.loads(raw.decode() or "{}")
            except json.JSONDecodeError:
                data = {}
            relay._result = data
            relay._event.set()
            self._json(200, {"ok": True})

    return H


def get_relay() -> Relay:
    global _relay
    if _relay is None:
        _relay = Relay()
        _relay.start()
    return _relay


def _dispatch(method: str, **params):
    if sys.platform == "darwin":
        return None  # caller uses osa
    return get_relay().call(method, **params)


# --- public tools ------------------------------------------------------------

def list_tabs() -> str:
    ensure_chrome()
    if sys.platform == "darwin":
        tabs = _osa_tabs()
    else:
        r = get_relay().call("tabs")
        if not r.get("ok"):
            return f"ERROR: {r.get('error')}"
        tabs = r.get("tabs") or []
    if not tabs:
        return "No tabs. Is Chrome open?"
    lines = [f"Found {len(tabs)} open tab(s):"]
    for i, t in enumerate(tabs):
        lines.append(f"  [{i}] {t.get('title','')} - {t.get('url','')}")
    return "\n".join(lines)


def switch_tab(target: str) -> str:
    ensure_chrome()
    if sys.platform == "darwin":
        tabs = _osa_tabs()
        idx = match_tab(tabs, target)
        if idx is None and str(target).isdigit():
            idx = int(target)
        if idx is None or not (0 <= idx < len(tabs)):
            return f"ERROR: No tab matching '{target}'"
        t = tabs[idx]
        _osa_activate(t["window"], t["tab"])
        return f"Switched to tab: {t['title']} ({t['url']})"
    r = get_relay().call("activate", target=str(target))
    if not r.get("ok"):
        return f"ERROR: {r.get('error')}"
    return f"Switched to tab: {r.get('title','')} ({r.get('url','')})"


def navigate(url: str) -> str:
    ensure_chrome()
    if "://" not in url:
        url = "https://" + url
    if sys.platform == "darwin":
        tabs = _osa_tabs()
        route = decide_route(tabs, url, current_index=0)
        if route.action == "reuse" and route.index is not None:
            t = tabs[route.index]
            _osa_activate(t["window"], t["tab"])
            if url.rstrip("/") in (t.get("url") or ""):
                return f"Reused tab {t['url']}"
            _osa_goto(url)
            return f"Navigated to {url}"
        if route.action == "new_tab":
            _osa_new_tab(url)
        else:
            _osa_goto(url)
        return f"Navigated to {url}"
    r = get_relay().call("navigate", url=url)
    if not r.get("ok"):
        return f"ERROR: {r.get('error')}"
    return f"Navigated to {url}"


def get_page_text() -> str:
    js = r"""(() => {
      const c = document.body.cloneNode(true);
      c.querySelectorAll('script,style,noscript,svg,nav,header,footer,[role=navigation],[aria-hidden=true]')
        .forEach(n => n.remove());
      return (c.innerText || '').replace(/\n{3,}/g, '\n\n').trim().slice(0, 4000);
    })()"""
    if sys.platform == "darwin":
        text = _osa_js(js)
    else:
        r = get_relay().call("eval", js=js)
        text = r.get("value") if r.get("ok") else f"ERROR: {r.get('error')}"
    if not text or str(text).startswith("error:") or str(text).startswith("ERROR:"):
        return f"ERROR: Failed to get page text ({text})"
    return f"Page text: {text}"


def snapshot() -> str:
    js = f"JSON.stringify(({SNAPSHOT_JS})(60))"
    if sys.platform == "darwin":
        raw = _osa_js(js)
    else:
        r = get_relay().call("eval", js=js)
        raw = r.get("value") if r.get("ok") else None
        if raw is None:
            return f"ERROR: {r.get('error')}"
    try:
        snap = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return f"ERROR: snapshot failed: {str(raw)[:200]}"
    els = (snap or {}).get("elements") or []
    _last_snap["elements"] = els
    _last_snap["labels"] = {e.get("index"): e.get("name", "") for e in els}
    return format_elements(snap)


def click_index(index) -> str:
    try:
        index = int(index)
    except (TypeError, ValueError):
        return f"ERROR: index must be a number, got {index!r}"
    from phase2_mcp.playwright_tools import _looks_risky
    label = _last_snap["labels"].get(index, "")
    if _looks_risky(label):
        return (f"BLOCKED: element [{index}] '{label}' looks like a purchase/submit/"
                f"irreversible control.")
    js = (
        f'(function(){{ var el=document.querySelector(\'[data-agent-index="{index}"]\');'
        f' if(!el) return "missing"; el.click(); return "ok"; }})()'
    )
    if sys.platform == "darwin":
        out = _osa_js(js)
    else:
        r = get_relay().call("eval", js=js)
        out = r.get("value") if r.get("ok") else f"ERROR: {r.get('error')}"
    if out == "ok":
        return f"Clicked [{index}] '{label}'"
    if out == "missing":
        return f"ERROR: [{index}] not on page — web_snapshot again."
    return f"ERROR: click failed ({out})"


def type_into_index(index, text) -> str:
    try:
        index = int(index)
    except (TypeError, ValueError):
        return f"ERROR: index must be a number, got {index!r}"
    payload = json.dumps(str(text))
    js = (
        f'(function(){{ var el=document.querySelector(\'[data-agent-index="{index}"]\');'
        f' if(!el) return "missing"; el.focus(); el.value={payload};'
        f' el.dispatchEvent(new Event("input",{{bubbles:true}})); return "ok"; }})()'
    )
    out = _osa_js(js) if sys.platform == "darwin" else (get_relay().call("eval", js=js).get("value"))
    if out == "ok":
        return f"Typed into [{index}]"
    return f"ERROR: could not type into [{index}] ({out})"


def press_key(key: str) -> str:
    k = json.dumps(key)
    js = f'(function(){{ document.activeElement && document.activeElement.dispatchEvent(new KeyboardEvent("keydown",{{key:{k},bubbles:true}})); return "ok"; }})()'
    _osa_js(js) if sys.platform == "darwin" else get_relay().call("eval", js=js)
    return f"Pressed '{key}'"


def scroll(direction: str = "down", clicks: int = 3) -> str:
    dy = abs(int(clicks)) * 400 * (1 if direction == "down" else -1)
    js = f"window.scrollBy(0, {dy}); 'ok'"
    _osa_js(js) if sys.platform == "darwin" else get_relay().call("eval", js=js)
    return f"Scrolled {direction} {clicks} ticks"


def install_hint() -> str:
    return (
        f"Load unpacked extension from:\n  {EXT_DIR}\n"
        "chrome://extensions → Developer mode → Load unpacked.\n"
        "No debugger permission: Chrome will not show the automation banner."
    )


def demo():
    raw = "1\t1\thttps://github.com/x\tGitHub\n1\t2\thttps://mail.google.com/mail\tInbox\n"
    tabs = _parse_tab_lines(raw)
    assert len(tabs) == 2 and tabs[1]["url"].startswith("https://mail.google.com")
    assert match_tab(tabs, "gmail") == 1
    r = decide_route(tabs, "https://mail.google.com")
    assert r.action == "reuse" and r.index == 1
    assert _js_blocked("error: Executing JavaScript through AppleScript is not allowed")
    assert not _js_blocked("ok")
    print("ok")


if __name__ == "__main__":
    demo()

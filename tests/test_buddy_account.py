"""Sign in with Google (through Supabase, PKCE, a one-shot page on 127.0.0.1) before Plip works."""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import time
import urllib.request
from urllib.parse import parse_qs, urlparse

from mcp_vision.buddy.account import Account
from mcp_vision.buddy.settings_service import Platform, SettingsService
from mcp_vision.buddy.store import Prefs

URL, KEY = "https://abc.supabase.co", "sb_publishable_test"
USER = {"id": "u1", "email": "ada@example.com", "created_at": "2026-10-06T12:00:00.5Z",
        "user_metadata": {"full_name": "Ada Lovelace", "avatar_url": "https://lh3.example/a=s96-c"},
        "app_metadata": {"provider": "google"}}
SESSION = {"access_token": "at1", "refresh_token": "rt1", "expires_at": 1, "user": USER}


class Settings:
    tts = stt = "auto"
    router = "rules"


class Supabase:
    """Supabase Auth stand-in: answers each call from a script and keeps what it was sent."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        answer = self.answers.pop(0) if self.answers else (200, {})
        if isinstance(answer, Exception):
            raise answer
        return answer


def account(tmp_path, *answers, **kwargs):
    supabase, opened, events = Supabase(*answers), [], []
    made = Account(URL, KEY, path=tmp_path / "account.json", open_url=opened.append, transport=supabase,
                   on_change=lambda: events.append("change"), on_signed_in=lambda: events.append("in"),
                   ports=kwargs.pop("ports", (0,)), fetch=kwargs.pop("fetch", lambda url: None), **kwargs)
    return made, supabase, opened, events


def browser(link, **query):
    """What the browser does after Google: Supabase sends it to redirect_to with these."""
    redirect = parse_qs(urlparse(link).query)["redirect_to"][0]
    with urllib.request.urlopen(redirect + ("?" + "&".join(f"{k}={v}" for k, v in query.items()) if query else ""),
                                timeout=5) as response:
        return response.read().decode()


def wait_closed(port):
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                pass
        except OSError:
            return True
        time.sleep(0.02)
    return False


def test_no_project_means_no_sign_in(tmp_path):
    plain = Account("", "", path=tmp_path / "account.json")
    assert not plain.available and not plain.required and not plain.blocker and not plain.start()
    assert plain.snapshot() == {"available": False, "required": False, "status": "", "error": "", "url": "",
                                "user": None}


def test_google_sign_in_with_pkce_keeps_the_session_private(tmp_path):
    acct, supabase, opened, events = account(tmp_path, (200, SESSION))
    assert acct.required and acct.blocker == "Sign in to Plip to start using it."
    assert acct.start("google")
    assert acct.status == "waiting" and opened == [acct.link] and acct.snapshot()["url"] == acct.link
    link = urlparse(acct.link)
    query = {name: values[0] for name, values in parse_qs(link.query).items()}
    assert f"{link.scheme}://{link.netloc}{link.path}" == f"{URL}/auth/v1/authorize"
    assert query["provider"] == "google" and query["code_challenge_method"] == "s256"
    assert query["redirect_to"].startswith("http://127.0.0.1:") and query["redirect_to"].endswith("/callback")
    page = browser(acct.link, code="the-code")
    assert "You’re signed in" in page
    method, url, headers, body = supabase.calls[0]
    assert (method, url) == ("POST", f"{URL}/auth/v1/token?grant_type=pkce")
    assert headers == {"apikey": KEY} and body["auth_code"] == "the-code"
    challenge = base64.urlsafe_b64encode(hashlib.sha256(body["code_verifier"].encode()).digest()).rstrip(b"=")
    assert challenge.decode() == query["code_challenge"]                   # only Plip had the verifier
    assert not acct.required and not acct.blocker and acct.status == "" and events[-1] == "in"
    assert acct.snapshot()["user"] == {"name": "Ada Lovelace", "email": "ada@example.com", "provider": "google",
                                       "since": 1791288000, "picture": ""}
    saved = json.loads((tmp_path / "account.json").read_text())
    assert saved["refresh_token"] == "rt1" and "at1" in json.dumps(saved)
    assert oct((tmp_path / "account.json").stat().st_mode & 0o777) == "0o600"
    assert wait_closed(int(urlparse(query["redirect_to"]).port))           # one-shot: the port is free again


def test_the_landing_page_runs_only_itself_and_keeps_the_code_out_of_sight(tmp_path):
    import re
    from pathlib import Path

    from mcp_vision.buddy import account as module

    acct, _supabase, _opened, _events = account(tmp_path, (200, {**SESSION, "user": {
        **SESSION["user"], "email": "<b>ada</b>@example.com"}}))
    acct.start()
    redirect = parse_qs(urlparse(acct.link).query)["redirect_to"][0]
    with urllib.request.urlopen(redirect + "?code=the-code", timeout=5) as response:
        headers, page = response.headers, response.read().decode()
    assert "&lt;b&gt;ada&lt;/b&gt;@example.com" in page and "<b>ada" not in page    # the email, escaped
    assert headers["Cache-Control"] == "no-store" and headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Frame-Options"] == "DENY" and headers["X-Content-Type-Options"] == "nosniff"
    csp = headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp and "unsafe" not in csp
    for tag, directive in (("style", "style-src"), ("script", "script-src")):        # the hashes match the page
        body = re.search(rf"<{tag}>(.*?)</{tag}>", page, re.S).group(1)
        digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
        assert f"{directive} 'sha256-{digest}'" in csp
    assert "history.replaceState" in page and "http" not in module.STYLE + module.SCRIPT      # nothing remote
    mascot = (Path(__file__).resolve().parents[1] / "ui" / "src" / "components" / "Mascot.tsx").read_text()
    shapes = re.findall(r"const (?:HOOD|FACE) = '([^']+)'", mascot)
    assert len(shapes) == 2 and all(f'd="{shape}"' in module.MARK for shape in shapes)   # Plip as in the app
    assert module.MARK in page


def test_a_declined_or_failed_sign_in_says_why_and_can_start_again(tmp_path):
    acct, supabase, _opened, _events = account(tmp_path, (400, {"error": "invalid_grant",
                                                                "error_description": "Code expired"}))
    acct.start()
    page = browser(acct.link, error="access_denied", error_description="You+said+no")
    assert "didn’t finish" in page and "You said no" in page
    assert acct.status == "failed" and acct.error == "You said no" and acct.required and supabase.calls == []
    acct.start()
    browser(acct.link, code="old")
    assert acct.status == "failed" and acct.error == "Code expired" and acct.required
    acct.start()
    assert acct.status == "waiting" and acct.error == ""
    acct.cancel()
    assert acct.status == "" and acct.snapshot()["url"] == ""


def test_supabase_errors_after_the_hash_come_back_as_a_query(tmp_path):
    acct, *_ = account(tmp_path)
    acct.start()
    assert "location.hash" in browser(acct.link)                              # no query yet: the page forwards it
    assert acct.status == "waiting"


def test_a_busy_port_moves_to_the_next_and_none_free_says_so(tmp_path):
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen()
    port = busy.getsockname()[1]
    try:
        acct, *_ = account(tmp_path, ports=(port, 0))
        assert acct.start() and f"127.0.0.1%3A{port}" not in acct.link
        acct.cancel()
        stuck, *_ = account(tmp_path, ports=(port,))
        assert not stuck.start() and stuck.status == "failed" and "ports" in stuck.error
    finally:
        busy.close()


def test_it_gives_up_after_a_while(tmp_path):
    acct, *_ = account(tmp_path, wait=0.05)
    acct.start()
    for _ in range(100):
        if acct.status == "failed":
            break
        time.sleep(0.02)
    assert acct.status == "failed" and "timed out" in acct.error


def test_launch_renews_the_session_and_a_removed_account_signs_out(tmp_path):
    renewed = {**SESSION, "access_token": "at2", "refresh_token": "rt2",
               "user": {**USER, "user_metadata": {"full_name": "Ada King"}}}
    acct, supabase, _opened, _events = account(tmp_path, (200, renewed), OSError("offline"), (400, {}))
    acct._keep(SESSION)
    acct.refresh()
    assert supabase.calls[0][1] == f"{URL}/auth/v1/token?grant_type=refresh_token"
    assert supabase.calls[0][3] == {"refresh_token": "rt1"} and acct.user["name"] == "Ada King"
    acct.refresh()                                                           # offline: still signed in
    assert acct.user is not None and supabase.calls[1][3] == {"refresh_token": "rt2"}
    acct.refresh()                                                           # Supabase says no: signed out
    assert acct.user is None and acct.required


PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 24


def picture(acct):
    for _ in range(100):
        if acct.snapshot()["user"]["picture"]:
            break
        time.sleep(0.01)
    return acct.snapshot()["user"]["picture"]


def test_the_google_picture_is_fetched_once_and_kept_here(tmp_path):
    fetched = []
    acct, _supabase, _opened, events = account(tmp_path, (200, SESSION), (200, SESSION),
                                               fetch=lambda url: fetched.append(url) or PNG)
    acct._keep(SESSION)
    assert picture(acct) == "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert fetched == ["https://lh3.example/a=s192-c"] and "change" in events      # sharp enough for the card
    acct.refresh()                                                                  # same picture: not again
    assert len(fetched) == 1 and picture(acct)
    moved = {**SESSION, "user": {**USER, "user_metadata": {"avatar_url": "https://lh3.example/b=s96-c"}}}
    acct._keep(moved)                                                               # a new one replaces it
    assert picture(acct) and fetched[-1] == "https://lh3.example/b=s192-c"
    acct.sign_out()
    assert not (tmp_path / "account.json").exists()


def test_no_picture_or_a_bad_one_keeps_the_initial(tmp_path):
    for answer in (None, b"<html>not an image</html>"):
        acct, *_ = account(tmp_path, fetch=lambda url, answer=answer: answer)
        acct._keep(SESSION)
        time.sleep(0.05)
        assert acct.snapshot()["user"]["picture"] == ""
    from mcp_vision.buddy.account import fetch_picture
    assert fetch_picture("http://lh3.example/a") is None and fetch_picture("file:///etc/passwd") is None


def test_sign_out_forgets_here_and_tells_supabase(tmp_path):
    acct, supabase, _opened, _events = account(tmp_path)
    acct._keep(SESSION)
    acct.sign_out()
    assert acct.required and not (tmp_path / "account.json").exists()
    for _ in range(100):
        if supabase.calls:
            break
        time.sleep(0.01)
    assert supabase.calls[0][:3] == ("POST", f"{URL}/auth/v1/logout?scope=local", {"apikey": KEY,
                                                                                  "Authorization": "Bearer at1"})


def test_settings_show_the_account_and_send_its_commands(tmp_path):
    path = tmp_path / "prefs.json"
    Prefs().save(path)
    acct, _supabase, opened, _events = account(tmp_path, (200, SESSION))
    posted, reloads = [], []
    svc = SettingsService(engines=lambda: [], settings=Settings, reload=lambda: reloads.append(1),
                          post=posted.extend, platform=Platform(open_url=opened.append), prefs_path=path,
                          account=acct)
    assert svc.snapshot()["account"]["required"] is True
    svc.handle({"cmd": "account-sign-in", "provider": "google"})
    assert posted[-1]["state"]["account"]["status"] == "waiting" and len(opened) == 1
    svc.handle({"cmd": "account-open"})
    assert opened[-1] == acct.link and len(opened) == 2
    svc.handle({"cmd": "account-cancel"})
    assert posted[-1]["state"]["account"]["status"] == ""
    acct._keep(SESSION)
    svc.handle({"cmd": "account-sign-out"})
    assert posted[-1]["state"]["account"] == {**posted[-1]["state"]["account"], "required": True, "user": None}
    assert reloads == []                                                   # the app follows on_change instead
    plain = SettingsService(engines=lambda: [], settings=Settings, reload=lambda: None, post=lambda _m: None,
                            prefs_path=path)
    assert plain.snapshot()["account"] == {"available": False, "required": False}


def test_the_real_transport_sends_json_with_the_key_and_reads_errors():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from mcp_vision.buddy.account import urllib_transport

    seen = []

    class Auth(BaseHTTPRequestHandler):
        def do_POST(self):                                                      # noqa: N802
            seen.append((self.path, self.headers["apikey"], json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            ok = self.path.endswith("pkce")
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(SESSION if ok else {"error_description": "nope"}).encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Auth)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, body = urllib_transport("POST", base + "/auth/v1/token?grant_type=pkce", {"apikey": KEY}, {"a": 1})
        assert status == 200 and body["user"]["email"] == "ada@example.com"
        status, body = urllib_transport("POST", base + "/auth/v1/token?grant_type=refresh_token", {"apikey": KEY}, {})
        assert status == 400 and body == {"error_description": "nope"}
        assert seen[0] == ("/auth/v1/token?grant_type=pkce", KEY, {"a": 1})
    finally:
        server.shutdown()
        server.server_close()

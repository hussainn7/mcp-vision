"""Sign in with Google (through Supabase, PKCE, a one-shot page on 127.0.0.1) before Plip works."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import socket
import threading
import time
import urllib.request
from urllib.parse import parse_qs, urlparse

from mcp_vision import analytics
from mcp_vision.buddy.account import Account
from mcp_vision.buddy.settings_service import Platform, SettingsService
from mcp_vision.buddy.store import Prefs

URL, KEY = "https://abc.supabase.co", "sb_publishable_test"
USER = {"id": "u1", "email": "ada@example.com", "created_at": "2026-10-06T12:00:00.5Z",
        "user_metadata": {"full_name": "Ada Lovelace", "avatar_url": "https://lh3.example/a=s96-c"},
        "app_metadata": {"provider": "google"}}
SESSION = {"access_token": "at1", "refresh_token": "rt1", "expires_at": 1, "user": USER}
ANON_USER = {"id": "u1", "email": "", "is_anonymous": True, "created_at": "2026-10-06T12:00:00Z",
             "user_metadata": {"name": "Quiet Nomad"}, "app_metadata": {"provider": "anonymous", "providers": ["anonymous"]}}
ANON_SESSION = {"access_token": "an1", "refresh_token": "rn1", "expires_at": 1, "user": ANON_USER}
# the same account after Google was linked: Supabase keeps "anonymous" first in providers
LINKED_USER = {**USER, "app_metadata": {"provider": "anonymous", "providers": ["anonymous", "google"]}}
LINKED_SESSION = {**SESSION, "user": LINKED_USER}
NO_ACCOUNT = {"available": False, "identified": False, "anonymous": False, "prompt": False}


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
        if callable(answer):
            answer = answer(method, url, headers, body)
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


def wait_for(check, tries=200):
    for _ in range(tries):
        if check():
            return True
        time.sleep(0.01)
    return False


def service_for(acct, path, perms=None):
    posted, asks = [], []
    svc = SettingsService(engines=lambda: [], settings=Settings, reload=lambda: None, post=posted.extend,
                          platform=Platform(permissions=lambda: perms or {}), prefs_path=path, account=acct,
                          background=lambda work: work(), on_ask=asks.append)
    return svc, posted, asks


def tracker(monkeypatch):
    tracked = []
    monkeypatch.setattr(analytics, "track", lambda event, props=None: tracked.append((event, props)))
    return tracked


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
    assert not plain.available and not plain.identified and not plain.anonymous and not plain.start()
    assert not plain.ensure_anonymous() and plain.user is None and plain.distinct_id == ""
    assert plain.snapshot() == {**NO_ACCOUNT, "status": "", "error": "", "url": "", "user": None}


def test_google_sign_in_with_pkce_keeps_the_session_private(tmp_path):
    acct, supabase, opened, events = account(tmp_path, (200, SESSION))
    assert not acct.identified and acct.user is None
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
    assert acct.identified and not acct.anonymous and acct.status == "" and events[-1] == "in"
    assert acct.distinct_id == "u1"
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
    assert acct.status == "failed" and acct.error == "You said no" and not acct.identified and supabase.calls == []
    acct.start()
    browser(acct.link, code="old")
    assert acct.status == "failed" and acct.error == "Code expired" and not acct.identified
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
    assert acct.user is None and not acct.identified


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


def test_sign_out_forgets_here_tells_supabase_and_becomes_a_guest(tmp_path):
    acct, supabase, _opened, _events = account(tmp_path, (200, {}), (200, {**ANON_SESSION, "user": {
        **ANON_USER, "id": "u2", "user_metadata": {"name": "Swift Scout"}}}))
    acct._keep(SESSION)
    acct.sign_out()
    assert not acct.identified and not (tmp_path / "account.json").exists()
    assert wait_for(lambda: len(supabase.calls) == 2 and acct.anonymous)
    assert supabase.calls[0][:3] == ("POST", f"{URL}/auth/v1/logout?scope=local", {"apikey": KEY,
                                                                                  "Authorization": "Bearer at1"})
    assert supabase.calls[1][:2] == ("POST", f"{URL}/auth/v1/signup")          # Plip keeps working, as somebody new
    assert acct.user["name"] == "Swift Scout" and acct.distinct_id == "u2"


def test_settings_show_the_account_and_send_its_commands(tmp_path):
    path = tmp_path / "prefs.json"
    Prefs().save(path)
    acct, _supabase, opened, _events = account(tmp_path, (200, SESSION))
    posted, reloads = [], []
    svc = SettingsService(engines=lambda: [], settings=Settings, reload=lambda: reloads.append(1),
                          post=posted.extend, platform=Platform(open_url=opened.append), prefs_path=path,
                          account=acct)
    assert svc.snapshot()["account"]["identified"] is False and svc.snapshot()["account"]["user"] is None
    svc.handle({"cmd": "account-sign-in", "provider": "google"})
    assert posted[-1]["state"]["account"]["status"] == "waiting" and len(opened) == 1
    svc.handle({"cmd": "account-open"})
    assert opened[-1] == acct.link and len(opened) == 2
    svc.handle({"cmd": "account-cancel"})
    assert posted[-1]["state"]["account"]["status"] == ""
    acct._keep(SESSION)
    svc.handle({"cmd": "account-sign-out"})
    assert posted[-1]["state"]["account"] == {**posted[-1]["state"]["account"], "identified": False, "user": None}
    assert reloads == []                                                   # the app follows on_change instead
    plain = SettingsService(engines=lambda: [], settings=Settings, reload=lambda: None, post=lambda _m: None,
                            prefs_path=path)
    assert plain.snapshot()["account"] == NO_ACCOUNT


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


# -- a guest first, Google later ---------------------------------------------------------------------------
def test_skipping_the_walkthrough_still_makes_a_guest_account_named_like_quiet_nomad(tmp_path, monkeypatch):
    path = tmp_path / "prefs.json"
    Prefs().save(path)
    tracked = tracker(monkeypatch)

    def signup(method, url, headers, body):                      # Supabase: an anonymous user with that name
        return 200, {**ANON_SESSION, "user": {**ANON_USER, "user_metadata": {"name": body["data"]["name"]}}}
    acct, supabase, _opened, events = account(tmp_path, signup)
    svc, posted, _asks = service_for(acct, path)
    svc.handle({"cmd": "finish-onboarding", "skipped": True})
    assert Prefs.load(path).onboarded is True and posted[-1]["state"]["onboarded"] is True
    method, url, headers, body = supabase.calls[0]
    assert (method, url, headers) == ("POST", f"{URL}/auth/v1/signup", {"apikey": KEY})
    name = body["data"]["name"]
    assert re.fullmatch(r"[A-Z][a-z]+ [A-Z][a-z]+", name) and acct.anonymous and not acct.identified
    assert "in" not in events
    shown = svc.snapshot()["account"]                           # the app pushes on the account's on_change
    assert shown["anonymous"] is True and shown["prompt"] is False
    assert shown["user"] == {"name": name, "email": "", "provider": "anonymous", "since": 1791288000, "picture": ""}
    saved = json.loads((tmp_path / "account.json").read_text())
    assert saved["access_token"] == "an1" and saved["user"]["id"] == "u1" and acct.distinct_id == "u1"
    assert not (tmp_path / "account.json.tmp").exists()                     # written whole, then moved in place
    assert tracked[0] == ("onboarding_step", {"step": "skipped", "account": ""})   # before the account existed
    svc.handle({"cmd": "finish-onboarding"})                                # again (Start using Plip): no second account
    assert len(supabase.calls) == 1


def test_offline_keeps_the_name_here_and_tries_the_server_next_launch(tmp_path):
    made = lambda m, u, h, body: (200, {**ANON_SESSION, "user": {**ANON_USER, "user_metadata": {"name": body["data"]["name"]}}})  # noqa: E731
    acct, supabase, _opened, events = account(tmp_path, OSError("offline"), made)
    assert acct.ensure_anonymous() and acct.anonymous and events == ["change"]
    name = acct.user["name"]
    assert re.fullmatch(r"[A-Z][a-z]+ [A-Z][a-z]+", name) and acct.distinct_id == ""
    assert acct.snapshot()["user"]["email"] == ""
    assert acct.ensure_anonymous(retry_only=True)                            # next launch: same name, now on the server
    assert supabase.calls[1][3] == {"data": {"name": name}} and acct.distinct_id == "u1" and acct.user["name"] == name
    acct.ensure_anonymous()                                                  # and no third try once it's there
    assert len(supabase.calls) == 2
    fresh, supabase, *_ = account(tmp_path / "other")
    assert not fresh.ensure_anonymous(retry_only=True) and supabase.calls == []   # launch never makes a new row


def test_a_purged_anonymous_row_stays_a_local_name_until_they_act(tmp_path):
    acct, supabase, _opened, _events = account(tmp_path, (401, {}), (200, {**ANON_SESSION, "user": {
        **ANON_USER, "id": "u3"}}))
    acct._keep(ANON_SESSION)
    acct.refresh()                                                           # Supabase: gone (purged)
    assert acct.anonymous and acct.distinct_id == "" and acct.user["name"] == "Quiet Nomad"
    assert not acct.ensure_anonymous(retry_only=True) and len(supabase.calls) == 1   # next launch: still no new row
    assert acct.ensure_anonymous() and acct.distinct_id == "u3"             # a sign-out or reinstall: then yes


def test_google_links_to_the_anonymous_account_so_nobody_is_counted_twice(tmp_path):
    def link(method, url, headers, body):                        # Supabase: here's where to send the browser
        parsed = urlparse(url)
        assert (method, parsed.path, body) == ("GET", "/auth/v1/user/identities/authorize", None)
        assert headers == {"apikey": KEY, "Authorization": "Bearer an2"}          # the renewed token, not the old
        query = {name: values[0] for name, values in parse_qs(parsed.query).items()}
        assert query["provider"] == "google" and query["skip_http_redirect"] == "true"
        assert query["code_challenge_method"] == "s256" and query["redirect_to"].endswith("/callback")
        return 200, {"url": f"{URL}/auth/v1/authorize?{parsed.query}&flow=link"}
    renewed = {**ANON_SESSION, "access_token": "an2", "refresh_token": "rn2"}
    acct, supabase, opened, events = account(tmp_path, (200, renewed), link, (200, LINKED_SESSION))
    acct._keep(ANON_SESSION)
    acct.prompt = True
    assert acct.anonymous and acct.start("google") and acct.status == "waiting"
    assert acct.link == "" and acct.snapshot()["url"] == ""                 # nothing to "open again" yet
    assert wait_for(lambda: opened) and opened == [acct.link] and "flow=link" in acct.link
    assert supabase.calls[0][1] == f"{URL}/auth/v1/token?grant_type=refresh_token"
    page = browser(acct.link, code="the-code")
    assert "You’re signed in" in page and "ada@example.com" in page
    assert supabase.calls[2][1] == f"{URL}/auth/v1/token?grant_type=pkce"
    assert acct.identified and not acct.anonymous and acct.prompt is False and events[-1] == "in"
    assert acct.distinct_id == "u1"                                          # the same account
    assert acct.snapshot()["user"] == {"name": "Ada Lovelace", "email": "ada@example.com", "provider": "google",
                                       "since": 1791288000, "picture": ""}


def test_linking_refused_by_the_project_signs_in_as_somebody_new(tmp_path):
    acct, _supabase, opened, _events = account(tmp_path, (200, ANON_SESSION), (403, {"msg": "linking off"}),
                                               (200, SESSION))
    acct._keep(ANON_SESSION)
    assert acct.start()
    assert wait_for(lambda: opened) and opened == [acct.link] and acct.link.startswith(f"{URL}/auth/v1/authorize?")
    browser(acct.link, code="the-code")
    assert acct.identified


def test_a_purged_anonymous_session_at_sign_in_goes_plain(tmp_path):
    acct, _supabase, opened, _events = account(tmp_path, (401, {}), (200, SESSION))
    acct._keep(ANON_SESSION)
    acct.start()
    assert wait_for(lambda: opened) and "identities" not in acct.link
    browser(acct.link, code="c")
    assert acct.identified


def test_offline_at_sign_in_says_so_instead_of_making_a_second_account(tmp_path):
    acct, _supabase, opened, _events = account(tmp_path, OSError("offline"))
    acct._keep(ANON_SESSION)
    assert acct.start()
    assert wait_for(lambda: acct.status == "failed") and opened == [] and "connection" in acct.error
    assert acct.anonymous and acct.distinct_id == "u1"


def test_a_google_that_already_has_an_account_signs_in_to_it(tmp_path):
    theirs = {**SESSION, "user": {**USER, "id": "u-old"}}
    linked = lambda m, url, h, b: (200, {"url": f"{URL}/auth/v1/authorize?{urlparse(url).query}&flow=link"})  # noqa: E731
    acct, supabase, opened, _events = account(tmp_path, (200, ANON_SESSION), linked, (200, theirs))
    acct._keep(ANON_SESSION)
    acct.start()
    assert wait_for(lambda: opened)
    first = acct.link
    page = browser(first, error="server_error", error_code="identity_already_exists",
                   error_description="Identity+is+already+linked+to+another+user")
    assert "already have a Plip account" in page
    assert wait_for(lambda: len(opened) == 2) and acct.status == "waiting"       # straight into a plain sign-in
    assert "identities" not in opened[1] and opened[1] != first and acct.user is None
    browser(acct.link, code="c")
    assert acct.identified and acct.distinct_id == "u-old" and len(supabase.calls) == 3


def test_a_cancelled_link_never_opens_the_browser_or_complains(tmp_path):
    gate = threading.Event()

    def slow(method, url, headers, body):
        gate.wait(2)
        raise OSError("late")
    acct, _supabase, opened, _events = account(tmp_path, slow)
    acct._keep(ANON_SESSION)
    acct.start()
    acct.cancel()
    gate.set()
    time.sleep(0.1)
    assert opened == [] and acct.status == ""


# -- the moments Plip asks: after the 1st and 3rd task, once for an update; three Laters and it stops -------
def test_plip_asks_for_google_after_the_first_and_third_task_then_an_update_then_stops(tmp_path, monkeypatch):
    path = tmp_path / "prefs.json"
    Prefs().save(path)
    tracked = tracker(monkeypatch)
    acct, _supabase, _opened, _events = account(tmp_path)
    acct._keep(ANON_SESSION)
    svc, posted, asks = service_for(acct, path)

    svc.task_done()                                                          # 1st task: the card, the app shows it
    assert acct.prompt and asks == ["first_task"] and posted[-1]["state"]["account"]["prompt"] is True
    assert ("first_task_done", {"account": "u1"}) in tracked
    assert tracked[-1] == ("signin_prompted", {"moment": "first_task", "tasks": 1, "account": "u1"})
    svc.task_done()                                                          # 2nd: nothing new
    assert asks == ["first_task"]
    svc.handle({"cmd": "account-later"})
    assert not acct.prompt and Prefs.load(path).signin_asks == 1
    assert tracked[-1] == ("signin_skipped", {"asks": 1, "tasks": 2, "account": "u1"})
    svc.task_done()                                                          # 3rd: again
    assert acct.prompt and asks == ["first_task", "third_task"]
    assert tracked[-1] == ("signin_prompted", {"moment": "third_task", "tasks": 3, "account": "u1"})
    svc.update_found()                                                       # closed the window instead of Later? asked again
    assert asks[-1] == "update" and Prefs.load(path).asked_on_update is True
    svc.handle({"cmd": "account-later"})
    svc.update_found()                                                       # the update moment was spent
    assert not acct.prompt and len(asks) == 3
    svc.handle({"cmd": "account-later"})                                     # nothing up: nothing counted
    assert Prefs.load(path).signin_asks == 2
    for _ in range(5):
        svc.task_done()
    prefs = Prefs.load(path)
    prefs.asked_on_update = False
    prefs.save(path)
    svc.update_found()                                                       # the third and last ask
    assert acct.prompt and Prefs.load(path).asked_on_update is True
    svc.handle({"cmd": "account-later"})
    assert Prefs.load(path).signin_asks == 3 and Prefs.load(path).tasks_done == 8
    prefs = Prefs.load(path)
    prefs.asked_on_update = False
    prefs.save(path)
    svc.update_found()
    svc.task_done()
    assert not acct.prompt and len(asks) == 4                                # three Laters: Plip stops asking
    assert Prefs.load(path).asked_on_update is False                         # and an unused moment isn't spent


def test_a_signed_in_or_waiting_account_is_never_asked(tmp_path):
    path = tmp_path / "prefs.json"
    Prefs().save(path)
    acct, _supabase, _opened, _events = account(tmp_path)
    acct._keep(SESSION)
    svc, _posted, asks = service_for(acct, path)
    svc.task_done()
    svc.update_found()
    assert not acct.prompt and asks == [] and Prefs.load(path).tasks_done == 1
    assert Prefs.load(path).asked_on_update is False                         # kept for when it could matter
    anon, *_ = account(tmp_path, lambda *a: (200, ANON_SESSION), lambda *a: (200, {"url": "x"}))
    anon._keep(ANON_SESSION)
    svc, _posted, asks = service_for(anon, path)
    anon.start()                                                             # already in the browser
    assert not svc.ask_sign_in("first_task") and asks == []
    anon.cancel()


def test_the_funnel_sends_each_step_once_per_install_with_the_account_along(tmp_path, monkeypatch):
    path = tmp_path / "prefs.json"
    Prefs().save(path)
    tracked = tracker(monkeypatch)
    acct, _supabase, _opened, _events = account(tmp_path)
    acct._keep(ANON_SESSION)
    perms = {"screenRecording": True, "accessibility": False, "microphone": None}
    engines = [{"id": "claude", "label": "Claude", "status": "logged-out", "selected": True}]
    svc = SettingsService(engines=lambda: engines, settings=Settings, reload=lambda: None, post=lambda _m: None,
                          platform=Platform(permissions=lambda: perms), prefs_path=path, account=acct)
    svc.push()
    svc.push()
    assert tracked == [("permission_granted", {"permission": "screen", "account": "u1"})]
    perms["accessibility"] = True
    engines[0]["status"] = "ready"
    svc.push()
    assert tracked[1:] == [("permission_granted", {"permission": "accessibility", "account": "u1"}),
                           ("engine_connected", {"engine": "claude", "account": "u1"})]
    svc.handle({"cmd": "tour-go", "step": "permissions"})                   # the tour moving is the funnel's start
    svc.handle({"cmd": "tour-go", "step": "permissions"})
    svc.handle({"cmd": "tour-go", "step": "made-up"})
    assert tracked[3:] == [("onboarding_step", {"step": "permissions", "account": "u1"})]
    svc.signed_in()
    assert tracked[-1] == ("signin_done", {"tasks": 0, "account": "u1"})
    assert sorted(Prefs.load(path).milestones) == ["engine", "onboarding:permissions", "permission:accessibility",
                                                   "permission:screen"]

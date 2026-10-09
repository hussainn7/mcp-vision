"""Plip accounts: sign in once with Google, after a few free asks in the welcome tour.

Sign-in goes through Supabase Auth with PKCE, the way a desktop app should (RFC 8252): Plip opens the
browser at Supabase's authorize page, Google sends the person back to Supabase, and Supabase sends them to a
one-shot page Plip serves on 127.0.0.1 with a code. Plip swaps that code (plus the secret only it holds)
for a session. Supabase keeps the list of accounts: Authentication → Users in its dashboard.

On this Mac, ``~/.config/mcp-vision/account.json`` (readable only by you) keeps who you are, your Google picture
(fetched once, for Settings) and the session. When Plip starts it renews the session once, so an account
removed in Supabase signs out here.

What leaves the Mac: the sign-in itself (Google tells Supabase your name, email and picture), that one
renewal per launch, and fetching the picture from Google when it's new. Nothing about what you ask Plip. Usage stats stay anonymous and are never tied to it.

No config (``PLIP_SUPABASE_URL`` + ``PLIP_SUPABASE_KEY``: the environment, ``~/.config/mcp-vision/.env``,
or baked into a release build) means no sign-in at all, and Plip works the same (running from source).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from mcp_vision import __version__

log = logging.getLogger("mcp_vision.buddy.account")

# Fixed ports, so Supabase's redirect allow list can name them: http://127.0.0.1:4782?/callback covers all three.
PORTS = (47823, 47824, 47825)
CALLBACK = "/callback"
WAIT = 600.0                                    # seconds to finish in the browser before giving up
PROVIDERS = {"google"}
SIGN_IN_FIRST = "Sign in to keep using Plip."
# Supabase saying "this session is over" (account deleted, banned, signed out everywhere): sign out here too.
GONE = {400, 401, 403, 404}
PICTURE_SIZE = 192                              # px asked of Google: sharp at 52 pt on retina
PICTURE_MAX = 2_000_000                         # bytes; Google's are ~10 KB
IMAGE_TYPES = {b"\xff\xd8\xff": "image/jpeg", b"\x89PNG\r\n\x1a\n": "image/png", b"GIF8": "image/gif"}

Transport = Callable[[str, str, dict[str, str], dict[str, Any] | None], tuple[int, dict[str, Any]]]


def urllib_transport(method: str, url: str, headers: dict[str, str], body: dict[str, Any] | None,
                     ) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json", "User-Agent": f"Plip/{__version__}", **headers})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            status, raw = int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        status, raw = int(exc.code), exc.read()
    try:
        parsed = json.loads(raw or b"{}")
    except ValueError:
        parsed = {}
    return status, parsed if isinstance(parsed, dict) else {}


def fetch_picture(url: str) -> bytes | None:
    """The picture's bytes, or None (https only, capped)."""
    if not url.startswith("https://"):
        return None
    request = urllib.request.Request(url, headers={"User-Agent": f"Plip/{__version__}"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = response.read(PICTURE_MAX + 1)
    except Exception:
        return None
    return data if len(data) <= PICTURE_MAX else None


def _image_type(data: bytes) -> str:
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return next((kind for magic, kind in IMAGE_TYPES.items() if data.startswith(magic)), "")


def pkce_pair() -> tuple[str, str]:
    """A fresh secret (verifier) and what the browser may see of it (the S256 challenge)."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _profile(user: dict[str, Any]) -> dict[str, Any]:
    """What Plip shows and keeps about the person: name, email, picture link, how they signed in, since when."""
    meta = user.get("user_metadata") or {}
    created = str(user.get("created_at") or "")
    try:
        since = int(datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()) if created else None
    except ValueError:
        since = None
    return {"id": str(user.get("id") or ""), "email": str(user.get("email") or meta.get("email") or ""),
            "name": str(meta.get("full_name") or meta.get("name") or ""),
            "picture_url": str(meta.get("avatar_url") or meta.get("picture") or ""),
            "provider": str((user.get("app_metadata") or {}).get("provider") or "google"), "since": since}


def _error_text(body: dict[str, Any], fallback: str) -> str:
    text = body.get("error_description") or body.get("msg") or body.get("message") or body.get("error") or fallback
    return str(text)[:200]


class Account:
    def __init__(self, url: str | None = "", key: str | None = "", *, path: Path | None = None,
                 open_url: Callable[[str], Any] = lambda url: None, transport: Transport = urllib_transport,
                 on_change: Callable[[], None] = lambda: None, on_signed_in: Callable[[], None] = lambda: None,
                 fetch: Callable[[str], bytes | None] = fetch_picture, trying: Callable[[], bool] = lambda: False,
                 ports: tuple[int, ...] = PORTS, wait: float = WAIT):
        from mcp_vision.buddy.store import config_dir

        self.base = (url or "").strip().rstrip("/")
        self.key = (key or "").strip()
        self.path = path or config_dir() / "account.json"
        self.open_url = open_url
        self.transport = transport
        self.on_change = on_change                 # status moved (the Settings window shows it)
        self.on_signed_in = on_signed_in           # signed in: Plip starts working
        self.fetch = fetch
        self.trying = trying                       # the tour's free tries: Plip works before sign-in
        self.ports = ports
        self.wait = wait
        self.status = ""                           # "" | waiting | failed
        self.error = ""
        self.link = ""                             # the sign-in page, to open again
        self._server: HTTPServer | None = None
        self._verifier = ""
        self._lock = threading.Lock()
        self._writing = threading.Lock()           # account.json: sign-in, the picture, sign-out

    # -- state -----------------------------------------------------------------------------------
    @property
    def available(self) -> bool:
        """This build has sign-in (a Supabase project is configured)."""
        return bool(self.base and self.key)

    def _session(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    @property
    def user(self) -> dict[str, Any] | None:
        user = self._session().get("user")
        return user if isinstance(user, dict) and user.get("email") else None

    @property
    def required(self) -> bool:
        """Sign-in stands between this person and Plip."""
        return self.available and self.user is None

    @property
    def blocker(self) -> str:
        """Why Plip won't take a request yet ("" once signed in, in a build without sign-in, or while trying)."""
        return SIGN_IN_FIRST if self.required and not self.trying() else ""

    def snapshot(self) -> dict[str, Any]:
        user = self.user
        shown = {key: user.get(key) for key in ("name", "email", "provider", "since")} if user else None
        if shown is not None:
            shown["picture"] = str(self._session().get("picture") or "")       # a data: url, or "" for the initial
        return {"available": self.available, "required": self.required, "status": self.status, "error": self.error,
                "url": self.link if self.status == "waiting" else "", "user": shown}

    def _save(self, session: dict[str, Any]) -> None:
        """Whole or not at all: Settings may be reading it while the picture lands."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(session, handle)
        os.chmod(temp, 0o600)
        os.replace(temp, self.path)

    def _keep(self, body: dict[str, Any]) -> bool:
        """Keep a session Supabase handed over. False when it isn't one."""
        user = body.get("user")
        if not body.get("access_token") or not isinstance(user, dict):
            return False
        profile = _profile(user)
        if not profile["email"]:
            return False
        with self._writing:
            old = self._session()
            same = (old.get("user") or {}).get("picture_url") == profile["picture_url"]
            picture = str(old.get("picture") or "") if same else ""
            self._save({"user": profile, "access_token": body["access_token"],
                        "refresh_token": body.get("refresh_token") or "", "expires_at": body.get("expires_at"),
                        "picture": picture})
        if profile["picture_url"] and not picture:
            threading.Thread(target=self._get_picture, args=(profile["picture_url"],), daemon=True,
                             name="plip-picture").start()
        return True

    def _get_picture(self, url: str) -> None:
        """Download their Google picture once and keep it here, so Settings never loads it from Google."""
        data = self.fetch(re.sub(r"=s\d+-c$", f"=s{PICTURE_SIZE}-c", url)) or b""
        kind = _image_type(data)
        if not kind:
            return
        with self._writing:
            session = self._session()
            if (session.get("user") or {}).get("picture_url") != url:
                return                                              # signed out or changed meanwhile
            session["picture"] = f"data:{kind};base64,{base64.b64encode(data).decode()}"
            self._save(session)
        self.on_change()

    def _headers(self, token: str = "") -> dict[str, str]:
        return {"apikey": self.key, **({"Authorization": f"Bearer {token}"} if token else {})}

    def _set(self, status: str, error: str = "") -> None:
        self.status, self.error = status, error
        self.on_change()

    # -- signing in ------------------------------------------------------------------------------
    def start(self, provider: str = "google") -> bool:
        """Open the browser at the sign-in page and wait for it to come back. False if it can't start."""
        if not self.available or provider not in PROVIDERS:
            return False
        self.cancel(quiet=True)
        server = self._listen()
        if server is None:
            self._set("failed", "Something else on this Mac is using Plip's sign-in ports. Quit it and try again.")
            return False
        self._verifier, challenge = pkce_pair()
        redirect = f"http://127.0.0.1:{server.server_address[1]}{CALLBACK}"
        self.link = f"{self.base}/auth/v1/authorize?" + urlencode({
            "provider": provider, "redirect_to": redirect, "code_challenge": challenge,
            "code_challenge_method": "s256"})
        with self._lock:
            self._server = server
        threading.Thread(target=server.serve_forever, daemon=True, name="plip-sign-in").start()
        timer = threading.Timer(self.wait, self._expire, args=(server,))
        timer.daemon = True
        timer.start()
        self._set("waiting")
        self.open_url(self.link)
        return True

    def _listen(self) -> HTTPServer | None:
        account = self

        class Callback(BaseHTTPRequestHandler):
            def do_GET(self):                                                   # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != CALLBACK:
                    self.send_error(404)
                    return
                query = {name: values[0] for name, values in parse_qs(parsed.query).items()}
                ok, message = account._arrived(self.server, query)
                page = _page(ok, message, waiting=not query)
                self.send_response(200)
                for name, value in PAGE_HEADERS.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(page.encode())

            def log_message(self, *args):                                       # keep codes out of the log
                pass

        for port in self.ports:
            try:
                return HTTPServer(("127.0.0.1", port), Callback)
            except OSError:
                continue
        return None

    def _arrived(self, server: HTTPServer, query: dict[str, str]) -> tuple[bool, str]:
        """The browser came back: swap the code for a session, or say what went wrong."""
        if not query:                         # an error Supabase put after the # : the page sends it back here
            return False, ""
        with self._lock:
            if server is not self._server:
                return False, "This sign-in page is out of date. Start again from Plip."
            self._server = None
        _close(server)
        if query.get("error") or not query.get("code"):
            message = _error_text(query, "Google sign-in didn't finish.")
            self._set("failed", message)
            return False, message
        try:
            status, body = self.transport("POST", f"{self.base}/auth/v1/token?grant_type=pkce", self._headers(),
                                          {"auth_code": query["code"], "code_verifier": self._verifier})
        except Exception as exc:
            log.info("account: sign-in exchange failed (%s)", type(exc).__name__)
            self._set("failed", "Couldn't reach Plip's sign-in server. Check your connection and try again.")
            return False, self.error
        if not (200 <= status < 300 and self._keep(body)):
            self._set("failed", _error_text(body, "Sign-in didn't go through. Try again."))
            return False, self.error
        self._verifier = ""
        self._set("")
        self.on_signed_in()
        return True, (self.user or {}).get("email", "")

    def _expire(self, server: HTTPServer) -> None:
        with self._lock:
            if server is not self._server:
                return
            self._server = None
        _close(server)
        self._set("failed", "Sign-in timed out. Try again when you're ready.")

    def cancel(self, quiet: bool = False) -> None:
        with self._lock:
            server, self._server = self._server, None
        if server is not None:
            _close(server)
        self._verifier = ""
        if not quiet:
            self._set("")

    # -- keeping it fresh, and signing out -------------------------------------------------------------------
    def refresh(self) -> None:
        """Renew the session (once per launch). A session Supabase won't renew signs out here; offline keeps it."""
        session = self._session()
        if not self.available or not session.get("refresh_token"):
            return
        try:
            status, body = self.transport("POST", f"{self.base}/auth/v1/token?grant_type=refresh_token",
                                          self._headers(), {"refresh_token": session["refresh_token"]})
        except Exception as exc:
            log.info("account: couldn't renew the session (%s), staying signed in", type(exc).__name__)
            return
        if 200 <= status < 300 and self._keep(body):
            self.on_change()
        elif status in GONE:
            log.info("account: Supabase ended the session (%s), signing out", status)
            self._forget()
            self.on_change()

    def sign_out(self) -> None:
        """Forget the account here right away, then tell Supabase (best effort, in the background)."""
        session = self._session()
        self._forget()
        self.cancel(quiet=True)
        self._set("")
        token = session.get("access_token")
        if self.available and token:
            def tell() -> None:
                try:
                    self.transport("POST", f"{self.base}/auth/v1/logout?scope=local", self._headers(token), None)
                except Exception:
                    pass
            threading.Thread(target=tell, daemon=True, name="plip-sign-out").start()

    def _forget(self) -> None:
        with self._writing:
            try:
                self.path.unlink()
            except OSError:
                pass


def _close(server: HTTPServer) -> None:
    """Stop serving and free the port. From another thread: it may be the one answering the browser."""
    def stop() -> None:
        server.shutdown()
        server.server_close()
    threading.Thread(target=stop, daemon=True, name="plip-sign-in-close").start()


# The tab the browser lands on, dressed like the welcome screen: ink, the mark's blue glow, the gradient title.
try:
    MARK = (Path(__file__).with_name("plip-mark.svg")).read_text()        # the Mascot's hood, face and eye
except OSError:
    MARK = ""
STYLE = """*{box-sizing:border-box}html,body{height:100%}
body{margin:0;display:grid;place-items:center;overflow:hidden;background:#06070a;color:#fff;
font:15px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",sans-serif;-webkit-font-smoothing:antialiased}
.glow{position:fixed;border-radius:50%;filter:blur(130px);pointer-events:none}
.glow.one{width:560px;height:560px;left:-12rem;top:-16rem;background:rgba(74,125,241,.17)}
.glow.two{width:460px;height:460px;right:-14rem;bottom:-12rem;background:rgba(122,167,247,.09)}
main{position:relative;max-width:420px;padding:24px;text-align:center;animation:rise .45s cubic-bezier(.32,.72,0,1) both}
.mark{position:relative;width:84px;height:95px;margin:0 auto 30px}
.mark:before{content:"";position:absolute;inset:-18%;z-index:-1;border-radius:50%;background:rgba(111,158,245,.3);filter:blur(26px)}
.mark svg{display:block;width:100%;height:100%}
.waiting .mark{animation:bob 1.8s ease-in-out infinite}
.failed .mark svg{filter:hue-rotate(128deg) saturate(1.2)}.failed .mark:before{background:rgba(229,72,106,.26)}
h1{margin:0 0 10px;font-size:34px;line-height:1.05;font-weight:600;letter-spacing:-.045em;color:transparent;
background:linear-gradient(100deg,#fff 15%,#bfd5fd 55%,#9cc2fa 80%,#a5b4fc 100%);-webkit-background-clip:text;background-clip:text}
.failed h1{background-image:linear-gradient(100deg,#fff 20%,#fbc4cf 70%,#f68ba0 100%)}
p{margin:0;color:rgba(255,255,255,.5)}
.who{display:inline-block;margin-top:20px;padding:6px 14px;border-radius:999px;font-size:13px;color:rgba(255,255,255,.75);
background:rgba(255,255,255,.05);box-shadow:inset 0 0 0 1px rgba(255,255,255,.1)}
.close{margin-top:34px;font-size:12.5px;color:rgba(255,255,255,.3)}
@keyframes rise{from{opacity:0;transform:translateY(10px);filter:blur(6px)}}@keyframes bob{50%{transform:translateY(-6px)}}
@media (prefers-reduced-motion:reduce){main,.waiting .mark{animation:none}}"""
# Errors Supabase puts after the # come back as a query; once the page has what it needs, the one-time code
# leaves the address bar and the history entry.
SCRIPT = (f'if(!location.search&&location.hash.length>1)location.replace("{CALLBACK}?"+location.hash.slice(1));'
          f'else if(location.search)history.replaceState(null,"","{CALLBACK}")')


def _hash(source: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(source.encode()).digest()).decode() + "'"


# Nothing loads from anywhere, nothing but this exact style and script runs, no other site can frame it, and
# the address (it carries the code) never goes out as a referrer or into a cache.
PAGE_HEADERS = {
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "no-store",
    "Content-Security-Policy": f"default-src 'none'; style-src {_hash(STYLE)}; script-src {_hash(SCRIPT)}; "
                               "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def _page(ok: bool, message: str, waiting: bool = False) -> str:
    """The tab the browser lands on: finishing, signed in (as whom), or why it didn't finish."""
    who = ""
    if waiting:
        state, title, text = "waiting", "Finishing sign-in…", "One moment."
    elif ok:
        state, title, text = "ok", "You’re signed in.", "Plip is ready. Head back to it."
        who = f'<div class="who">{escape(message)}</div>' if message else ""
    else:
        state, title, text = "failed", "Sign-in didn’t finish.", (message or "Something went wrong on the way back.")
    close = "" if waiting else '<p class="close">' + (
        "You can close this tab." if ok else "Close this tab and try again from Plip.") + "</p>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Plip</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark">
<style>{STYLE}</style></head><body class="{state}"><div class="glow one"></div><div class="glow two"></div>
<main><div class="mark">{MARK}</div><h1>{escape(title)}</h1><p>{escape(text)}</p>{who}{close}</main>
<script>{SCRIPT}</script></body></html>"""


__all__ = ["Account", "PORTS", "pkce_pair"]

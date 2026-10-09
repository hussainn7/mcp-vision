"""One click to connect a brain: install its CLI if it's missing, sign in in the browser, switch to it.

No Terminal and nothing to copy. The installer and the CLI's own sign-in run in
the background; the CLI opens the browser, and Plip watches the CLI's status
until it says signed in, then makes it the brain. If the browser doesn't open,
the sign-in link (and a box for the code some pages hand back) shows up in Settings.

    connector = Connector(probe=..., on_change=push_settings, on_connected=use_engine)
    connector.start("claude-code")
"""
from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from mcp_vision.buddy.engines import BY_ID, EngineStatus, child_env

URL_RE = re.compile(r"https://\S+")
CODE_PROMPT = re.compile(r"paste (the )?code", re.IGNORECASE)
# ChatGPT's Codex CLI is one binary per Mac on GitHub. The release's API entry names the file and its SHA-256, and
# the download is checked against it before anything is unpacked.
CODEX_RELEASE = "https://api.github.com/repos/openai/codex/releases/latest"
CODEX_DOWNLOADS = "https://github.com/openai/codex/releases/download/"


@dataclass
class Progress:
    state: str = "idle"          # installing | signing-in | ready | failed | cancelled
    message: str = ""
    url: str = ""                # the sign-in page, for when the browser didn't open
    needs_code: bool = False     # the page shows a code to paste back

    def card(self) -> dict[str, Any]:
        data = asdict(self)
        data["needsCode"] = data.pop("needs_code")
        return data


class ConnectError(RuntimeError):
    """Something a person can act on: said in Settings as-is."""


class Connector:
    def __init__(self, *, probe: Callable[[str], EngineStatus], on_change: Callable[[], None] = lambda: None,
                 on_connected: Callable[[str], None] = lambda engine_id: None,
                 spawn: Callable[..., Any] = subprocess.Popen, run: Callable[..., Any] = subprocess.run,
                 download: Callable[[str, Path], None] | None = None,
                 fetch_json: Callable[[str], Any] | None = None, home: Path | None = None,
                 timeout: float = 300.0, poll: float = 1.5):
        self.probe = probe
        self.on_change = on_change
        self.on_connected = on_connected
        self.spawn = spawn
        self.run = run
        self.download = download or _download
        self.fetch_json = fetch_json or _fetch_json
        self.home = home or Path.home()
        self.timeout = timeout
        self.poll = poll
        self.progress: dict[str, Progress] = {}
        self._process: dict[str, Any] = {}
        self._cancelled: set[str] = set()
        self._workers: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    # -- what Settings calls ------------------------------------------------------------
    def start(self, engine_id: str, *, wait: bool = False) -> None:
        if engine_id not in BY_ID or BY_ID[engine_id].kind != "subscription":
            return
        with self._lock:
            running = self._workers.get(engine_id)
            if running is not None and running.is_alive():
                return              # already on it, or a cancelled install still finishing: never two at once
            self._cancelled.discard(engine_id)
            self.progress[engine_id] = Progress("installing", "Getting ready…")
            worker = self._workers[engine_id] = threading.Thread(target=self._connect, args=(engine_id,),
                                                                 daemon=True, name=f"plip-connect-{engine_id}")
        worker.start()
        if wait:
            worker.join()

    def cancel(self, engine_id: str) -> None:
        self._cancelled.add(engine_id)
        self._stop(engine_id)
        if self.progress.get(engine_id, Progress()).state in {"installing", "signing-in"}:
            self._set(engine_id, "cancelled", "")

    def send_code(self, engine_id: str, code: str) -> None:
        """The code the sign-in page showed, for CLIs that ask for it."""
        process = self._process.get(engine_id)
        code = code.strip()
        if process is None or not code or process.stdin is None:
            return
        try:
            process.stdin.write((code + "\n").encode())
            process.stdin.flush()
        except (OSError, ValueError):
            return
        current = self.progress.get(engine_id)
        if current is not None:
            self._set(engine_id, current.state, "Checking the code…", url=current.url)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {engine_id: progress.card() for engine_id, progress in self.progress.items()
                if progress.state not in {"idle", "cancelled"}}

    # -- the work -------------------------------------------------------------------------
    def _connect(self, engine_id: str) -> None:
        spec = BY_ID[engine_id]
        try:
            status = self.probe(engine_id)
            if status.status == "not-installed":
                self._set(engine_id, "installing", f"Installing {spec.label}’s app… (about a minute)")
                self._install(engine_id)
                if engine_id in self._cancelled:
                    return                              # cancelled while it installed: no browser after that
                status = self.probe(engine_id)
                if status.status == "not-installed":
                    raise ConnectError(f"{spec.label} installed, but I can't find it. Try again in a moment.")
            if status.status != "ready":
                self._sign_in(engine_id, status)
            if engine_id in self._cancelled:
                return
            status = self.probe(engine_id)
            if status.status != "ready":                # the sign-in app quit, but it isn't signed in
                raise ConnectError(f"Signing in to {spec.label} didn't finish. Click Connect to try again.")
            self._set(engine_id, "ready", status.detail or "Connected")
            self.on_connected(engine_id)
        except ConnectError as exc:
            if engine_id not in self._cancelled:
                self._set(engine_id, "failed", str(exc))
        except Exception as exc:                        # never leave the card spinning
            if engine_id not in self._cancelled:
                self._set(engine_id, "failed", f"Couldn't connect {spec.label}: {exc}")
        finally:
            self._stop(engine_id)

    def _install(self, engine_id: str) -> None:
        env = child_env()
        if engine_id == "claude-code":
            self._shell("curl -fsSL https://claude.ai/install.sh | bash", env, "Claude Code")
        elif engine_id == "cursor":
            self._shell("curl -fsS https://cursor.com/install | bash", env, "Cursor's CLI")
        elif engine_id == "codex":
            self._install_codex()
        elif engine_id == "gemini":
            npm = shutil.which("npm", path=env["PATH"])
            if npm is None:
                raise ConnectError("Gemini's app needs Node.js, which isn't on this Mac. Connect Claude or ChatGPT "
                                   "instead, or install Node from nodejs.org and try again.")
            self._shell(f'"{npm}" install -g --prefix "{self.home / ".local"}" @google/gemini-cli', env, "Gemini CLI")

    def _shell(self, command: str, env: dict[str, str], what: str) -> None:
        try:
            # pipefail: "curl … | bash" with no internet fails here, not later as "installed, but I can't find it"
            result = self.run(["/bin/bash", "-c", "set -o pipefail; " + command], env=env, cwd=str(self.home),
                              capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            raise ConnectError(f"Installing {what} took too long. Check your internet and try again.") from None
        if result.returncode != 0:
            tail = " ".join((result.stderr or result.stdout or "").strip().splitlines()[-2:])[-200:]
            raise ConnectError(f"Couldn't install {what}. Check your internet and try again."
                               + (f" ({tail})" if tail else ""))

    def _install_codex(self) -> None:
        """ChatGPT's Codex CLI is one binary on GitHub: no Node needed. Checked against the release's SHA-256."""
        arch = "aarch64" if platform.machine() in {"arm64", "aarch64"} else "x86_64"
        name = f"codex-{arch}-apple-darwin.tar.gz"
        target = self.home / ".local" / "bin" / "codex"
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "codex.tar.gz"
            try:
                release = self.fetch_json(CODEX_RELEASE)
                asset = next((item for item in release.get("assets") or [] if item.get("name") == name), None)
                url, digest = (asset or {}).get("browser_download_url") or "", (asset or {}).get("digest") or ""
                if not url.startswith(CODEX_DOWNLOADS) or not digest.startswith("sha256:"):
                    raise ConnectError("Couldn't find ChatGPT's app for this Mac on GitHub. Try again later.")
                self.download(url, archive)
                if _sha256(archive) != digest.removeprefix("sha256:").lower():
                    raise ConnectError("ChatGPT's app didn't download correctly (its checksum doesn't match). "
                                       "Try again.")
                with tarfile.open(archive) as bundle:
                    files = [item for item in bundle.getmembers() if item.isfile()]
                    member = next((item for item in files if item.name.endswith(f"codex-{arch}-apple-darwin")),
                                  files[0] if len(files) == 1 else None)
                    if member is None:
                        raise ConnectError("The ChatGPT download didn't have the app in it. Try again.")
                    member.name = "codex"
                    bundle.extract(member, tmp, filter="data")
            except ConnectError:
                raise
            except Exception as exc:
                raise ConnectError(f"Couldn't download ChatGPT's app ({exc}). Check your internet and try again.") \
                    from None
            shutil.move(str(Path(tmp) / "codex"), target)
        target.chmod(0o755)

    def _sign_in(self, engine_id: str, status: EngineStatus) -> None:
        spec = BY_ID[engine_id]
        path = status.path or shutil.which(spec.binaries[0], path=child_env()["PATH"])
        if not path:
            raise ConnectError(f"I can't find {spec.label}'s app. Try again.")
        argv = {"claude-code": [path, "auth", "login"], "codex": [path, "login"], "cursor": [path, "login"],
                "gemini": [path, "-p", "Reply with the single word: ok"]}[engine_id]
        if engine_id == "gemini":
            self._gemini_google_login()
        self._set(engine_id, "signing-in", f"Finish signing in to {spec.label} in your browser…")
        process = self.spawn(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             env=child_env(path), cwd=str(self.home))
        self._process[engine_id] = process
        threading.Thread(target=self._watch_output, args=(engine_id, process), daemon=True).start()
        started = time.monotonic()
        while True:
            if engine_id in self._cancelled:
                return
            if self.probe(engine_id).status == "ready":
                return
            code = process.poll()
            if code is not None and code != 0 and self.probe(engine_id).status != "ready":
                raise ConnectError(f"Signing in to {spec.label} didn't finish. Try again.")
            if code == 0:
                return                                   # the CLI says it's done; the final probe confirms
            if time.monotonic() - started > self.timeout:
                raise ConnectError("Sign-in timed out. Click Connect to try again.")
            time.sleep(self.poll)

    def _watch_output(self, engine_id: str, process: Any) -> None:
        """Pick the sign-in link and any "paste code" prompt out of the CLI's output.

        Reads whatever has arrived rather than whole lines: a prompt waiting for input
        ("Paste code here if prompted > ") has no newline after it.
        """
        stream = process.stdout
        if stream is None:
            return
        read = getattr(stream, "read1", None) or (lambda size: stream.read(size))
        seen = ""
        while True:
            try:
                chunk = read(4096)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            seen = (seen + (chunk.decode("utf-8", "replace") if isinstance(chunk, bytes) else str(chunk)))[-4000:]
            current = self.progress.get(engine_id)
            if current is None or current.state != "signing-in":
                continue
            found = next((match.group(0).rstrip(".,)") for match in URL_RE.finditer(seen)
                          if match.end() < len(seen)), "")             # a link that's been printed in full
            url = current.url or found
            needs_code = current.needs_code or bool(CODE_PROMPT.search(seen))
            if (url, needs_code) != (current.url, current.needs_code):
                self._set(engine_id, "signing-in", current.message, url=url, needs_code=needs_code)

    def _gemini_google_login(self) -> None:
        """Gemini signs in on first use once "Login with Google" is the chosen method."""
        import json

        settings = self.home / ".gemini" / "settings.json"
        try:
            data = json.loads(settings.read_text()) if settings.exists() else {}
        except ValueError:
            data = {}
        security = data.setdefault("security", {})
        auth = security.setdefault("auth", {}) if isinstance(security, dict) else {}
        if isinstance(auth, dict) and not auth.get("selectedType"):
            auth["selectedType"] = "oauth-personal"
            settings.parent.mkdir(parents=True, exist_ok=True)
            settings.write_text(json.dumps(data, indent=2))

    # -- bookkeeping ------------------------------------------------------------------------
    def _set(self, engine_id: str, state: str, message: str, *, url: str = "", needs_code: bool = False) -> None:
        with self._lock:
            self.progress[engine_id] = Progress(state, message, url, needs_code)
        try:
            self.on_change()
        except Exception:
            pass

    def _stop(self, engine_id: str) -> None:
        process = self._process.pop(engine_id, None)
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=3)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "Plip", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Plip"})
    with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as handle:
        shutil.copyfileobj(response, handle)


__all__ = ["ConnectError", "Connector", "Progress"]

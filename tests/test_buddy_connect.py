"""One-click Connect: install the brain's app if needed, sign in in the browser, switch to it."""
from __future__ import annotations

import io
import subprocess
import tarfile
import threading
from types import SimpleNamespace

from mcp_vision.buddy.connect import Connector
from mcp_vision.buddy.engines import BY_ID, EngineStatus


class FakeLogin:
    """CLI sign-in: prints a link and code prompt, then waits."""

    def __init__(self, lines=(), code=None):
        self.stdout = io.BytesIO(b"".join(line.encode() + b"\n" for line in lines))
        self.stdin = io.BytesIO()
        self.code = code
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else self.code

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminated = True


class Brain:
    """Probe results that advance: not installed -> signed out -> ready."""

    def __init__(self, *states):
        self.states = list(states)
        self.lock = threading.Lock()

    def __call__(self, engine_id):
        with self.lock:
            state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return EngineStatus(BY_ID[engine_id], state, path=None if state == "not-installed" else "/bin/claude",
                            detail="Signed in as you · max" if state == "ready" else "")


def connector(probe, tmp_path, **kw):
    changes, connected = [], []
    made = Connector(probe=probe, on_change=lambda: changes.append(1), on_connected=connected.append,
                     home=tmp_path, poll=0.01, **kw)
    return made, changes, connected


def test_signed_out_claude_signs_in_in_the_browser_and_becomes_the_brain(tmp_path):
    login = FakeLogin(["Opening browser to sign in…",
                       "If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true&x=1",
                       "Paste code here if prompted >"])
    spawned = []
    made, changes, connected = connector(Brain("logged-out", "logged-out", "logged-out", "ready"), tmp_path,
                                         spawn=lambda argv, **kw: spawned.append(argv) or login)
    made.start("claude-code", wait=True)
    assert spawned == [["/bin/claude", "auth", "login"]] and connected == ["claude-code"]
    assert made.progress["claude-code"].state == "ready" and "Signed in" in made.progress["claude-code"].message
    assert login.terminated and changes


def test_the_sign_in_link_and_code_box_show_up_while_waiting(tmp_path):
    login = FakeLogin(["If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true.",
                       "Paste code here if prompted >"])
    seen = []
    made, _, _ = connector(Brain("logged-out"), tmp_path, spawn=lambda argv, **kw: login, timeout=0.3)
    made.on_change = lambda: seen.append(dict(made.snapshot().get("claude-code", {})))
    made.start("claude-code", wait=True)
    waiting = [state for state in seen if state.get("state") == "signing-in" and state.get("needsCode")]
    assert waiting and waiting[-1]["url"] == "https://claude.com/cai/oauth/authorize?code=true"
    assert made.progress["claude-code"].state == "failed" and "timed out" in made.progress["claude-code"].message


def test_pasting_the_code_goes_to_the_sign_in(tmp_path):
    made, _, _ = connector(Brain("logged-out"), tmp_path)
    login = FakeLogin()
    made._process["claude-code"] = login
    made.send_code("claude-code", " abc#123 ")
    assert login.stdin.getvalue() == b"abc#123\n"


def test_missing_claude_is_installed_first(tmp_path):
    ran = []
    made, _, connected = connector(
        Brain("not-installed", "logged-out", "logged-out", "ready"), tmp_path,
        run=lambda argv, **kw: ran.append(argv) or SimpleNamespace(returncode=0, stdout="", stderr=""),
        spawn=lambda argv, **kw: FakeLogin())
    made.start("claude-code", wait=True)
    assert ran == [["/bin/bash", "-c", "set -o pipefail; curl -fsSL https://claude.ai/install.sh | bash"]]
    assert connected == ["claude-code"]


def codex_tarball(payload=b"#!/bin/sh\necho codex\n", name="codex-aarch64-apple-darwin"):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        info = tarfile.TarInfo(name)
        info.size = len(payload)
        bundle.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def codex_release(data, digest=None):
    import hashlib
    import platform

    arch = "aarch64" if platform.machine() in {"arm64", "aarch64"} else "x86_64"
    return {"assets": [{"name": f"codex-{arch}-apple-darwin.tar.gz",
                        "browser_download_url": f"https://github.com/openai/codex/releases/download/v1/codex-{arch}.tar.gz",
                        "digest": digest or "sha256:" + hashlib.sha256(data).hexdigest()}]}


def test_codex_comes_straight_from_github_checked_against_its_sha256_no_node_needed(tmp_path):
    import platform

    arch = "aarch64" if platform.machine() in {"arm64", "aarch64"} else "x86_64"
    data = codex_tarball(name=f"codex-{arch}-apple-darwin")
    fetched = []

    def download(url, target):
        fetched.append(url)
        target.write_bytes(data)
    made, _, connected = connector(Brain("not-installed", "ready"), tmp_path, download=download,
                                   fetch_json=lambda url: codex_release(data))
    made.start("codex", wait=True)
    binary = tmp_path / ".local" / "bin" / "codex"
    assert fetched[0].startswith("https://github.com/openai/codex/releases/download/")
    assert binary.read_bytes().startswith(b"#!/bin/sh") and binary.stat().st_mode & 0o111
    assert connected == ["codex"]


def test_a_codex_download_that_doesnt_match_its_checksum_is_never_installed(tmp_path):
    data = codex_tarball()
    made, _, connected = connector(Brain("not-installed"), tmp_path,
                                   download=lambda url, target: target.write_bytes(codex_tarball(b"#!/bin/sh\nevil\n")),
                                   fetch_json=lambda url: codex_release(data))
    made.start("codex", wait=True)
    assert made.progress["codex"].state == "failed" and "checksum" in made.progress["codex"].message
    assert not (tmp_path / ".local" / "bin" / "codex").exists() and connected == []
    made, _, _ = connector(Brain("not-installed"), tmp_path, download=lambda url, target: None,
                           fetch_json=lambda url: {"assets": [{"name": "x", "browser_download_url": "https://evil.example/c"}]})
    made.start("codex", wait=True)
    assert made.progress["codex"].state == "failed" and not (tmp_path / ".local" / "bin" / "codex").exists()


def test_a_failed_install_is_explained(tmp_path):
    made, _, _ = connector(Brain("not-installed"), tmp_path,
                           run=lambda argv, **kw: SimpleNamespace(returncode=1, stdout="", stderr="curl: (6) no host"))
    made.start("cursor", wait=True)
    assert made.progress["cursor"].state == "failed" and "no host" in made.progress["cursor"].message


def test_an_install_that_hangs_times_out(tmp_path):
    def hang(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
    made, _, _ = connector(Brain("not-installed"), tmp_path, run=hang)
    made.start("claude-code", wait=True)
    assert "took too long" in made.progress["claude-code"].message


def test_cancel_stops_the_sign_in(tmp_path):
    login = FakeLogin()
    made, _, connected = connector(Brain("logged-out"), tmp_path, spawn=lambda argv, **kw: login, timeout=5)
    made.start("claude-code")
    for _ in range(200):
        if "claude-code" in made._process:
            break
        threading.Event().wait(0.01)
    made.cancel("claude-code")
    threading.Event().wait(0.1)
    assert login.terminated and connected == [] and made.snapshot() == {}


def test_api_engines_and_unknown_ids_are_ignored(tmp_path):
    made, _, _ = connector(Brain("missing-key"), tmp_path)
    made.start("anthropic", wait=True)
    made.start("nope", wait=True)
    assert made.progress == {}


def test_settings_connect_command_starts_it_and_cards_show_progress(tmp_path):
    from mcp_vision.buddy.settings import BuddySettings
    from mcp_vision.buddy.settings_service import SettingsService
    from mcp_vision.buddy.store import History

    started, posted = [], []

    class Fake:
        def start(self, engine_id):
            started.append(engine_id)

        def snapshot(self):
            return {"claude-code": {"state": "signing-in", "message": "Finish signing in…", "url": "", "needsCode": False}}

    engines = [{"id": "claude-code", "label": "Claude", "status": "logged-out"}, {"id": "codex", "status": "ready"}]
    service = SettingsService(engines=lambda: engines, settings=lambda: BuddySettings(_env_file=None),
                              reload=lambda: None, post=posted.extend, prefs_path=tmp_path / "prefs.json",
                              env_path=tmp_path / ".env", history=History(path=tmp_path / "h.jsonl"), connector=Fake())
    service.handle({"cmd": "engine-connect", "id": "claude-code"})
    cards = posted[-1]["state"]["engines"]
    assert started == ["claude-code"] and cards[0]["connect"]["state"] == "signing-in" and "connect" not in cards[1]


def test_cancel_during_the_install_never_opens_the_browser_and_never_runs_two_at_once(tmp_path):
    installing, release, spawned = threading.Event(), threading.Event(), []

    def install(argv, **kw):
        installing.set()
        release.wait(2)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    made, _, connected = connector(Brain("not-installed", "logged-out"), tmp_path, run=install,
                                   spawn=lambda argv, **kw: spawned.append(argv) or FakeLogin())
    made.start("claude-code")
    assert installing.wait(2)
    made.cancel("claude-code")
    made.start("claude-code")                          # Connect again mid-install
    release.set()
    made._workers["claude-code"].join(2)
    assert spawned == [] and connected == [] and made.snapshot() == {}


def test_a_sign_in_app_that_quits_without_signing_in_isnt_connected(tmp_path):
    made, _, connected = connector(Brain("logged-out"), tmp_path, spawn=lambda argv, **kw: FakeLogin(code=0))
    made.start("claude-code", wait=True)
    assert made.progress["claude-code"].state == "failed" and "didn't finish" in made.progress["claude-code"].message
    assert connected == [] and made.progress["claude-code"].url == ""


def test_a_failed_sign_in_keeps_its_link_so_they_can_finish_in_any_browser(tmp_path):
    login = FakeLogin(["Otherwise navigate to: https://claude.com/cai/oauth/authorize?x=2", ""], code=1)
    made, _, connected = connector(Brain("logged-out"), tmp_path, spawn=lambda argv, **kw: login)
    made.start("claude-code", wait=True)
    failed = made.progress["claude-code"]
    assert failed.state == "failed" and "didn't finish" in failed.message and connected == []
    assert failed.url == "https://claude.com/cai/oauth/authorize?x=2"          # copy it, paste it anywhere
    assert failed.card()["url"] == failed.url


def test_a_browser_the_sign_in_app_couldnt_open_gets_opened_by_plip(tmp_path):
    login = FakeLogin(["Otherwise navigate to: https://claude.com/cai/oauth/authorize?x=1",
                       "Failed to open browser with error: spawn open ENOENT", ""])
    opened = []
    made, _, connected = connector(Brain("logged-out", "logged-out", "logged-out", "ready"), tmp_path,
                                   spawn=lambda argv, **kw: login, open_url=opened.append)
    made.start("claude-code", wait=True)
    assert opened == ["https://claude.com/cai/oauth/authorize?x=1"] and connected == ["claude-code"]


def test_cancel_stops_the_whole_sign_in_app_not_just_its_launcher(tmp_path):
    import os
    import sys
    import time

    child = tmp_path / "child.pid"
    script = (f"import os, subprocess, sys, time; c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
              f"open({str(child)!r}, 'w').write(str(c.pid)); c.wait()")              # a launcher that relaunches itself
    made, _, _ = connector(Brain("logged-out"), tmp_path, timeout=10,
                           spawn=lambda argv, **kw: subprocess.Popen([sys.executable, "-c", script], **kw))
    made.start("claude-code")
    for _ in range(300):
        if child.exists() and child.read_text():
            break
        time.sleep(0.01)
    made.cancel("claude-code")
    pid = int(child.read_text())
    for _ in range(300):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.01)
    else:
        os.kill(pid, 9)
        raise AssertionError("the relaunched child outlived cancel")

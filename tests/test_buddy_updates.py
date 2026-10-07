"""A newer Plip: asked of GitHub once a day, offered in the menu bar and Settings, told once in the notch."""
from __future__ import annotations

import json

from mcp_vision.buddy.settings_service import Platform, SettingsService
from mcp_vision.buddy.store import Prefs
from mcp_vision.buddy.updates import RELEASES, Updates, newer, version_tuple

DMG = RELEASES + "download/v0.9.0/Plip-0.9.0.dmg"
RELEASE = {"tag_name": "v0.9.0", "draft": False, "prerelease": False, "html_url": RELEASES + "tag/v0.9.0",
           "assets": [{"name": "Plip-0.9.0.dmg", "browser_download_url": DMG}]}


class Settings:
    tts = stt = "auto"
    router = "rules"


class GitHub:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), 0

    def __call__(self):
        self.calls += 1
        answer = self.answers.pop(0) if self.answers else RELEASE
        if isinstance(answer, Exception):
            raise answer
        return answer


def updates(tmp_path, *answers, current="0.8.0", enabled=True):
    now, told, github = [1000.0], [], GitHub(*answers)
    made = Updates(current, path=tmp_path / "updates.json", fetch=github, enabled=lambda: enabled,
                   on_found=told.append, clock=lambda: now[0])
    return made, github, told, now


def test_versions_compare_like_numbers():
    assert version_tuple("v0.10.0") == (0, 10, 0) and version_tuple("0.9.0-beta") is None
    assert newer("v0.10.0", "0.9.0") and newer("0.9", "0.8.9") and newer("1.0.0", "0.99")
    assert not newer("0.8.0", "0.8.0") and not newer("0.8", "0.8.0") and not newer("0.7.1", "0.8.0")
    assert not newer("nightly", "0.8.0") and not newer("0.9.0", "")


def test_a_newer_release_is_offered_and_told_once(tmp_path):
    made, github, told, now = updates(tmp_path)
    found = made.check()
    assert found == {"version": "0.9.0", "url": DMG, "page": RELEASES + "tag/v0.9.0"}
    assert told == [found] and github.calls == 1
    assert made.check() == found and github.calls == 1 and len(told) == 1      # same day: GitHub isn't asked
    now[0] += 24 * 60 * 60
    assert made.check() == found and github.calls == 2 and len(told) == 1      # next day: asked, not told again
    assert made.snapshot() == {"enabled": True, "current": "0.8.0", "available": found}
    assert json.loads((tmp_path / "updates.json").read_text())["told"] == "0.9.0"


def test_up_to_date_drafts_and_betas_offer_nothing(tmp_path):
    same, *_ = updates(tmp_path / "a", {**RELEASE, "tag_name": "v0.8.0"})
    assert same.check() is None
    for odd in ({**RELEASE, "prerelease": True}, {**RELEASE, "draft": True}, {**RELEASE, "tag_name": "v1.0-rc1"}, {}):
        made, _github, told, _now = updates(tmp_path / str(len(str(odd))), odd)
        assert made.check() is None and told == []


def test_offline_keeps_the_last_answer_and_says_nothing_new(tmp_path):
    made, github, told, now = updates(tmp_path, RELEASE, OSError("offline"))
    made.check()
    now[0] += 2 * 24 * 60 * 60
    assert made.check()["version"] == "0.9.0" and github.calls == 2 and len(told) == 1


def test_download_only_ever_opens_plips_own_releases(tmp_path):
    elsewhere = {**RELEASE, "html_url": "https://evil.example/plip",
                 "assets": [{"name": "Plip.dmg", "browser_download_url": "https://evil.example/Plip.dmg"}]}
    made, *_ = updates(tmp_path, elsewhere)
    assert made.check() == {"version": "0.9.0", "url": RELEASES + "latest", "page": RELEASES + "latest"}
    no_dmg, *_ = updates(tmp_path / "b", {**RELEASE, "assets": []})
    assert no_dmg.check()["url"] == RELEASES + "tag/v0.9.0"                    # no DMG yet: the release page


def test_turned_off_never_asks(tmp_path):
    made, github, told, _now = updates(tmp_path, enabled=False)
    assert made.check() is None and made.check(force=True) is None and github.calls == 0 and told == []
    assert made.snapshot()["available"] is None


def test_settings_show_it_download_it_or_stop_asking(tmp_path):
    path = tmp_path / "prefs.json"
    Prefs().save(path)
    github, opened, posted, checks = GitHub(), [], [], []
    made = Updates("0.8.0", path=tmp_path / "updates.json", fetch=github, enabled=lambda: Prefs.load(path).update_check)
    svc = SettingsService(engines=lambda: [], settings=Settings, reload=lambda: None, post=posted.extend,
                          platform=Platform(open_url=opened.append), prefs_path=path, updates=made,
                          check_updates=lambda: checks.append(made.check(force=True)))
    svc.handle({"cmd": "update-download"})
    assert opened == []                                                        # nothing found yet
    made.check()
    assert svc.snapshot()["update"]["available"]["version"] == "0.9.0"
    svc.handle({"cmd": "update-download"})
    assert opened == [DMG]
    svc.handle({"cmd": "set-update-check", "enabled": False})
    assert Prefs.load(path).update_check is False and checks == [None]
    assert posted[-1]["state"]["update"] == {"enabled": False, "current": "0.8.0", "available": None}
    svc.handle({"cmd": "update-download"})
    assert opened == [DMG]
    plain = SettingsService(engines=lambda: [], settings=Settings, reload=lambda: None, post=lambda _m: None,
                            prefs_path=path)
    assert plain.snapshot()["update"]["available"] is None


def test_the_real_request_reads_githubs_answer():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from mcp_vision.buddy.updates import github_latest

    seen = []

    class Api(BaseHTTPRequestHandler):
        def do_GET(self):                                                       # noqa: N802
            seen.append((self.path, self.headers["Accept"], self.headers["User-Agent"]))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(RELEASE).encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Api)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        data = github_latest(f"http://127.0.0.1:{server.server_address[1]}/repos/x/releases/latest")
        assert data["tag_name"] == "v0.9.0"
        assert seen[0][0] == "/repos/x/releases/latest" and "github" in seen[0][1] and seen[0][2].startswith("Plip/")
    finally:
        server.shutdown()
        server.server_close()

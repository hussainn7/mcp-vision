"""Plip's app layer without AppKit: presenter, settings backend, prefs, web bridge, island/mascot math."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from mcp_vision.buddy.animator import RenderState
from mcp_vision.buddy.app_macos import applescript_string
from mcp_vision.buddy.controller import BuddyController
from mcp_vision.buddy.factory import apply_prefs
from mcp_vision.buddy.island_macos import island_hit, notch_geometry
from mcp_vision.buddy.mascot_macos import mascot_state
from mcp_vision.buddy.presenter import Presenter
from mcp_vision.buddy.settings import BuddySettings
from mcp_vision.buddy.settings_service import Platform, SettingsService
from mcp_vision.buddy.store import History, Prefs
from mcp_vision.buddy.web_host import parse_command, script_for


# -- presenter -------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def presenter():
    posted, moods, clock = [], [], Clock()
    return Presenter(posted.extend, lambda mood, level: moods.append((mood, level)), clock), posted, moods, clock


def test_presenter_push_to_talk_flow():
    view, posted, moods, clock = presenter()
    view.listening()
    assert posted[:2] == [{"type": "reset"}, {"type": "island", "state": {"phase": "listening"}}]
    clock.now = 1.0
    view.level(1.7)
    view.level(0.2)                                 # throttled: same instant
    assert posted[-1] == {"type": "island", "state": {"level": 1.0}}
    assert moods[-1] == ("listening", 1.0)
    view.transcript("where is export")
    view.thinking()
    assert posted[-2:] == [{"type": "island", "state": {"transcript": "where is export"}},
                           {"type": "island", "state": {"phase": "thinking", "level": 0}}]
    assert moods[-1] == ("thinking", 0.0)
    view.failed("I didn't catch that.")
    assert posted[-1]["state"] == {"phase": "error", "error": "I didn't catch that."}


def test_presenter_turns_companion_events_into_island_messages():
    view, posted, moods, _ = presenter()
    view("phase", {"phase": "thinking", "transcript": "how do I export", "guide": False})
    view("engine", {"label": "Claude", "kind": "subscription", "model": None, "extra": 1})
    view("step", {"id": "look", "label": "Looked at 2 screens", "status": "done", "detail": ""})
    view("phase", {"phase": "answering"})
    view("answer", {"text": "Open File."})
    view("walkthrough", {"index": 1, "total": 3, "label": "File menu", "waiting": True})
    view("done", {"latency_ms": 812.4})
    view("point", {"x": 1, "y": 2})                       # native animator owns flight
    view("unknown", {})
    assert posted[0]["state"]["transcript"] == "how do I export"
    assert posted[1] == {"type": "island", "state": {"engine": {"label": "Claude", "kind": "subscription",
                                                                "model": None}}}
    assert posted[2] == {"type": "step", "step": {"id": "look", "label": "Looked at 2 screens", "status": "done"}}
    assert posted[4] == {"type": "append", "field": "answer", "text": "Open File."}
    assert posted[5]["state"]["walkthrough"] == {"index": 1, "total": 3, "label": "File menu"}
    assert posted[6]["step"]["id"] == "wait" and posted[6]["step"]["status"] == "active"
    assert posted[-1] == {"type": "island", "state": {"done": True, "latencyMs": 812}}
    assert ("speaking", 0.5) in moods and moods[-1] == ("idle", 0.0)    # walkthrough still running

    view("phase", {"phase": "thinking", "guide": True})                  # check-in keeps progress
    assert posted[-1]["state"]["walkthrough"]["index"] == 1 and posted[-1]["state"]["answer"] == ""
    view("walkthrough", {"index": 3, "total": 3, "finished": True})
    assert moods[-1] == ("happy", 0.0)
    view("error", {"message": "Sign in first."})
    assert posted[-1]["state"] == {"phase": "error", "error": "Sign in first."}


# -- controller drives the presenter ---------------------------------------------------------

class Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, *args))


class Overlay:
    def set_state(self, state, detail=""):
        pass


def test_controller_mirrors_push_to_talk_in_the_presenter():
    view = Recorder()
    needed = []
    controller = BuddyController(companion=None, overlay=Overlay(), loop=None, call_later=lambda d, f: None,
                                 on_main=lambda f, *a: f(*a), presenter=view, setup_error="No brain yet.",
                                 on_setup_needed=needed.append, say=lambda text: None)
    controller.on_press()
    assert view.calls == [("failed", "No brain yet.")] and needed == ["No brain yet."]


def test_controller_hooks_and_result_callback():
    import asyncio
    import threading

    from mcp_vision.buddy.companion import TurnResult

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    class Companion:
        def interrupt(self, token=None):
            pass

        def prefetch(self):
            pass

        async def respond(self, text, token=None):
            return TurnResult(transcript=text, spoken="It's under File.", state="done")

    class Listener:
        def start(self): pass
        def release(self): pass
        def cancel(self): pass

    view, results, main_calls = Recorder(), [], []
    controller = BuddyController(companion=Companion(), overlay=Overlay(), loop=loop, listener=Listener(),
                                 call_later=lambda d, f: None, on_main=lambda f, *a: main_calls.append((f, a)),
                                 presenter=view, on_result=lambda q, r: results.append((q, r.spoken)))
    controller.on_press()
    controller.on_level(0.4)
    controller.on_partial("where is")
    controller.on_release()
    controller.on_final("where is export")
    import time
    deadline = time.monotonic() + 2
    while not main_calls and time.monotonic() < deadline:
        time.sleep(0.01)
    for fn, args in main_calls:
        fn(*args)
    names = [call[0] for call in view.calls]
    assert names == ["listening", "level", "transcript", "thinking", "transcript"]
    assert results == [("where is export", "It's under File.")]
    loop.call_soon_threadsafe(loop.stop)


# -- prefs, history, settings ------------------------------------------------------------------

def test_prefs_round_trip_and_tolerate_junk(tmp_path):
    path = tmp_path / "prefs.json"
    assert Prefs.load(path) == Prefs()
    Prefs(engine="codex", depth="deep", walkthroughs=False, buddy=False).save(path)
    assert Prefs.load(path).engine == "codex" and Prefs.load(path).buddy is False
    path.write_text(json.dumps({"engine": "cursor", "future_field": 1}))
    assert Prefs.load(path).engine == "cursor"
    path.write_text("{not json")
    assert Prefs.load(path) == Prefs()


def test_history_keeps_the_latest_items(tmp_path):
    history = History(path=tmp_path / "h.jsonl", limit=3)
    for index in range(8):
        history.add(f"q{index}", f"a{index}", engine="Claude")
    history.add("", "skipped")
    items = history.items()
    assert [item["question"] for item in items] == ["q5", "q6", "q7"]
    history.clear()
    assert history.items() == []


def test_apply_prefs_maps_depth_and_voice():
    settings = BuddySettings(_env_file=None)
    changed = apply_prefs(settings, Prefs(engine="codex", depth="deep", tts="say", stt="apple"))
    assert (changed.engine, changed.effort, changed.tts, changed.stt) == ("codex", "high", "say", "apple")
    explicit = BuddySettings(_env_file=None, effort="max")
    assert apply_prefs(explicit, Prefs(depth="fast")).effort == "max"     # env wins over the slider
    assert apply_prefs(settings, None) is settings


ENGINES = [{"id": "claude-code", "label": "Claude", "login": "claude auth login"},
           {"id": "anthropic", "label": "Claude API"}]


@pytest.fixture
def service(tmp_path, monkeypatch):
    from mcp_vision.buddy.memory import Memory

    for name in ("ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY", "ASSEMBLYAI_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    calls = {"reload": 0, "posted": [], "platform": [], "refresh": 0}
    platform = Platform(
        copy=lambda text: calls["platform"].append(("copy", text)),
        open_url=lambda url: calls["platform"].append(("open", url)),
        run_in_terminal=lambda cmd: calls["platform"].append(("terminal", cmd)),
        request_permission=lambda name: calls["platform"].append(("grant", name)),
        permissions=lambda: {"screenRecording": True, "accessibility": False, "microphone": None},
        say=lambda text: calls["platform"].append(("say", text)),
        quit=lambda: calls["platform"].append(("quit",)),
        open_settings=lambda tab: calls["platform"].append(("settings", tab)))
    svc = SettingsService(
        engines=lambda: ENGINES, settings=lambda: BuddySettings(_env_file=None),
        reload=lambda: calls.__setitem__("reload", calls["reload"] + 1), post=calls["posted"].extend,
        platform=platform, prefs_path=tmp_path / "prefs.json", env_path=tmp_path / ".env",
        history=History(path=tmp_path / "history.jsonl"),
        on_refresh=lambda: calls.__setitem__("refresh", calls["refresh"] + 1),
        memory=Memory(tmp_path / "memory.json"), run_import=lambda source: calls.setdefault("imports", []).append(source))
    return svc, calls, tmp_path


def test_settings_snapshot_shape(service):
    svc, calls, _ = service
    svc.handle({"cmd": "settings-ready"})
    snapshot = calls["posted"][-1]["state"]
    assert calls["refresh"] == 1
    assert snapshot["engines"] == ENGINES and snapshot["depth"] == "balanced" and snapshot["walkthroughs"] is True
    assert snapshot["permissions"] == {"screen": True, "accessibility": False, "microphone": None, "speech": None}
    assert snapshot["voice"]["tts"] == "say" and snapshot["voice"]["stt"] == "apple"
    assert snapshot["keys"]["ANTHROPIC_API_KEY"] is False and snapshot["history"] == []


def test_settings_commands_persist_and_reload(service):
    svc, calls, tmp_path = service
    svc.handle({"cmd": "select-engine", "id": "claude-code"})
    svc.handle({"cmd": "select-engine", "id": "made-up"})
    svc.handle({"cmd": "set-depth", "depth": "deep"})
    svc.handle({"cmd": "set-depth", "depth": "ludicrous"})
    svc.handle({"cmd": "set-walkthroughs", "enabled": False})
    svc.handle({"cmd": "set-voice", "tts": "off", "stt": "nope"})
    prefs = Prefs.load(tmp_path / "prefs.json")
    assert (prefs.engine, prefs.depth, prefs.walkthroughs, prefs.tts, prefs.stt) == (
        "claude-code", "deep", False, "off", "")
    assert calls["reload"] == 4


def test_settings_keys_are_whitelisted_and_private(service):
    svc, calls, tmp_path = service
    svc.handle({"cmd": "set-key", "name": "ANTHROPIC_API_KEY", "value": "  sk-ant-test  "})
    svc.handle({"cmd": "set-key", "name": "PATH", "value": "/evil"})
    svc.handle({"cmd": "set-key", "name": "ELEVENLABS_API_KEY", "value": "a\nB=c"})
    env = (tmp_path / ".env").read_text()
    assert env == "ANTHROPIC_API_KEY=sk-ant-test\n"
    assert oct((tmp_path / ".env").stat().st_mode & 0o777) == "0o600"
    assert calls["reload"] == 1


def test_settings_platform_actions_are_guarded(service):
    svc, calls, _ = service
    svc.handle({"cmd": "engine-login", "id": "claude-code"})
    svc.handle({"cmd": "engine-login", "id": "anthropic"})               # no login command
    svc.handle({"cmd": "copy", "text": "npm i -g @openai/codex"})
    svc.handle({"cmd": "open-url", "url": "https://console.anthropic.com"})
    svc.handle({"cmd": "open-url", "url": "file:///etc/passwd"})
    svc.handle({"cmd": "open-url", "url": "javascript:alert(1)"})
    svc.handle({"cmd": "grant", "permission": "screen"})
    svc.handle({"cmd": "grant", "permission": "camera"})
    svc.handle({"cmd": "test-voice"})
    svc.handle({"cmd": "open-settings", "tab": "voice"})
    svc.handle({"cmd": "no-such-command"})
    svc.handle({"cmd": "quit"})
    kinds = [call[0] for call in calls["platform"]]
    assert kinds == ["terminal", "copy", "open", "grant", "say", "settings", "quit"]
    assert calls["platform"][0] == ("terminal", "claude auth login")


def test_settings_history_and_clear(service):
    svc, calls, _ = service
    svc.history.add("where is wifi", "Top right.", engine="Claude")
    svc.push()
    assert calls["posted"][-1]["state"]["history"][0]["question"] == "where is wifi"
    svc.handle({"cmd": "clear-history"})
    assert calls["posted"][-1]["state"]["history"] == []


# -- web bridge, island and mascot math ------------------------------------------------------------

def test_bridge_parses_commands_and_escapes_scripts():
    assert parse_command('{"cmd": "ready", "surface": "island"}') == {"cmd": "ready", "surface": "island"}
    assert parse_command({"cmd": "quit"}) == {"cmd": "quit"}
    assert parse_command("[1, 2]") is None and parse_command("nope") is None
    assert parse_command('{"cmd": 3}') is None
    script = script_for([{"type": "append", "field": "answer", "text": "</script> \"quoted\" ✓"}])
    assert script.startswith("window.__plip && window.__plip([")
    assert json.loads(script.split("window.__plip(", 1)[1][:-1])[0]["text"] == "</script> \"quoted\" ✓"


class FakeScreen:
    def __init__(self, top_inset, aux=None, frame=(0, 0, 1512, 982), visible_top=949):
        self._inset, self._aux, self._frame, self._visible_top = top_inset, aux, frame, visible_top

    def safeAreaInsets(self):
        return SimpleNamespace(top=self._inset)

    def frame(self):
        x, y, w, h = self._frame
        return SimpleNamespace(origin=SimpleNamespace(x=x, y=y), size=SimpleNamespace(width=w, height=h))

    def visibleFrame(self):
        x, y, w, _ = self._frame
        return SimpleNamespace(origin=SimpleNamespace(x=x, y=y), size=SimpleNamespace(width=w, height=self._visible_top))

    def auxiliaryTopLeftArea(self):
        return SimpleNamespace(size=SimpleNamespace(width=self._aux[0]))

    def auxiliaryTopRightArea(self):
        return SimpleNamespace(size=SimpleNamespace(width=self._aux[1]))


def test_notch_geometry_reads_the_hardware_notch_or_fakes_one():
    assert notch_geometry(FakeScreen(32, aux=(658, 659))) == {"width": 195, "height": 32, "hasNotch": True}
    assert notch_geometry(FakeScreen(32, aux=(10, 10))) == {"width": 200, "height": 32, "hasNotch": True}
    assert notch_geometry(FakeScreen(0, frame=(0, 0, 2560, 1440), visible_top=1415)) == {
        "width": 190, "height": 25, "hasNotch": False}


def test_island_hit_follows_the_islands_current_size():
    window = (376.0, 642.0, 760.0, 340.0)            # centered at the top of a 1512 x 982 display
    assert island_hit((756, 975), window, (271, 32))
    assert not island_hit((756, 900), window, (271, 32))      # below the compact island
    assert island_hit((756, 900), window, (520, 180))         # inside the expanded card
    assert not island_hit((300, 975), window, (520, 180))


def render(mode="follow", x=100.0, y=100.0):
    return RenderState(x=x, y=y, angle=0, scale=1, mode=mode, voice="idle", level=0, bubble="", bubble_alpha=0)


def test_mascot_state_looks_leans_and_labels():
    idle = mascot_state(render(), "idle", 0.0, (0, 0), (100, 50), "")
    assert idle["mood"] == "idle" and idle["look"] == {"x": 0.0, "y": -1.0} and idle["label"] == ""
    flying = mascot_state(render("fly_out"), "thinking", 0.3, (30, 0), (0, 0), "File menu")
    assert flying["mood"] == "pointing" and flying["lean"] == 16.0 and flying["look"]["x"] == 1.0
    assert flying["label"] == ""                                # bubble waits for the landing
    landed = mascot_state(render("pointing"), "speaking", 0.5, (0, 0), (0, 0), "File menu")
    assert landed["label"] == "File menu" and landed["look"] == {"x": -0.7, "y": -0.7}


def test_applescript_strings_are_escaped():
    assert applescript_string('say "hi" \\ there') == '"say \\"hi\\" \\\\ there"'


def test_settings_memory_commands(service):
    svc, calls, tmp_path = service
    svc.handle({"cmd": "memory-paste", "source": "chatgpt", "text": "Name: Hussain Syed\nDiet: vegetarian"})
    panel = calls["posted"][-1]["state"]["memory"]
    assert panel["profile"]["name.full"] == "Hussain Syed" and panel["imports"]["chatgpt"]["count"] == 2
    assert {fact["value"] for fact in panel["facts"]} >= {"Hussain Syed", "Diet: vegetarian"}
    svc.handle({"cmd": "memory-add", "key": "email", "value": "h@example.com"})
    fact_id = next(fact["id"] for fact in calls["posted"][-1]["state"]["memory"]["facts"] if fact["key"] == "email")
    svc.handle({"cmd": "memory-delete", "id": fact_id})
    assert all(fact["key"] != "email" for fact in calls["posted"][-1]["state"]["memory"]["facts"])
    svc.handle({"cmd": "memory-forget-source", "source": "chatgpt"})
    assert calls["posted"][-1]["state"]["memory"]["facts"] == []
    svc.handle({"cmd": "memory-import", "source": "contacts"})
    svc.handle({"cmd": "memory-import", "source": "keychain"})            # not a source
    assert calls["imports"] == ["contacts"]
    svc.handle({"cmd": "memory-copy-prompt"})
    assert calls["platform"][-1][0] == "copy" and "key: value" in calls["platform"][-1][1]
    svc.handle({"cmd": "memory-paste", "text": "x" * 70_000})               # too big: ignored
    assert oct((tmp_path / "memory.json").stat().st_mode & 0o777) == "0o600"


def test_settings_skills_and_companion(service):
    svc, calls, tmp_path = service
    svc.handle({"cmd": "set-skill", "skill": "travel", "enabled": False})
    svc.handle({"cmd": "set-skill", "skill": "rockets", "enabled": False})
    svc.handle({"cmd": "set-companion", "style": "cursor"})
    svc.handle({"cmd": "set-companion", "style": "giant"})
    state = calls["posted"][-1]["state"]
    assert state["skills"]["travel"] is False and state["skills"]["apps"] is True
    assert state["companion"] == "cursor"
    assert "phone" not in state
    assert calls["reload"] == 2


def test_notch_home_droplet_drips_out_points_and_returns():
    from mcp_vision.buddy.animator import BuddyAnimator

    animator = BuddyAnimator(home=lambda mouse: (756.0, 20.0))
    render = animator.tick(0.0, (100.0, 100.0))
    assert (render.x, render.y) == (756.0, 20.0) and animator.docked
    animator.point(300, 400, "Export")
    assert not animator.docked
    modes, now = [], 0.0
    while now < 15:
        now += 1 / 60
        render = animator.tick(now, (100 + now * 80, 100.0))          # the user keeps moving the mouse
        if not modes or modes[-1] != render.mode:
            modes.append(render.mode)
    assert modes == ["fly_out", "pointing", "fly_back", "follow"]
    assert animator.docked and (round(render.x), round(render.y)) == (756, 20)


def test_cursor_style_still_trails_the_cursor():
    from mcp_vision.buddy.animator import FOLLOW_OFFSET, BuddyAnimator

    animator = BuddyAnimator()
    render = animator.tick(0.0, (100.0, 100.0))
    assert (render.x, render.y) == (100 + FOLLOW_OFFSET[0], 100 + FOLLOW_OFFSET[1]) and not animator.docked

import asyncio

from mcp_vision.context import Context
from mcp_vision.contextual import infer_capability
from mcp_vision.native_apps import (open_app, parse_intent, perform, resolve_app,
                                    shortcut_for, switch)
from mcp_vision.request_routing import route_request
from mcp_vision.tasks import ContextTask


def test_static_aliases_resolve_without_a_filesystem():
    assert resolve_app("Notes").bundle_id == "com.apple.Notes"
    assert resolve_app("the Calculator app").bundle_id == "com.apple.calculator"
    assert resolve_app("Finder").bundle_id == "com.apple.finder"


def test_filesystem_resolution_finds_an_installed_app(tmp_path):
    (tmp_path / "Figma.app").mkdir()
    target = resolve_app("Figma", aliases={}, app_dirs=(str(tmp_path),))
    assert target is not None and target.path.endswith("Figma.app")
    assert resolve_app("Nonexistent", aliases={}, app_dirs=(str(tmp_path),)) is None


def test_intent_parsing_is_conservative():
    assert parse_intent("open Notes").action == "open_app"
    assert parse_intent("open my notes").action == "open_app"
    assert parse_intent("Launch Calculator").action == "open_app"
    assert parse_intent("switch tabs").action == "switch_tab"
    assert parse_intent("Switch to the next tab").value == "next"
    assert parse_intent("previous tab").value == "prev"
    assert parse_intent("switch to tab 3").action == "switch_to_tab"
    assert parse_intent("next window").action == "switch_window"
    # Never hijack UI-element or web/URL requests.
    assert parse_intent("open the export menu") is None
    assert parse_intent("open a new tab") is None
    assert parse_intent("open Gmail") is None
    assert parse_intent("open https://example.com") is None
    assert parse_intent("fill this form") is None


def test_native_intents_are_actions_not_questions():
    for prompt in ("Open Notes", "switch tabs", "Switch to tab 3", "next window"):
        assert infer_capability(prompt) == "act"
        assert route_request(prompt, "act").kind == "native"


def test_browser_open_still_wins_for_web_products():
    assert route_request("Open Gmail", "act").kind == "browser_open"
    assert route_request("Open https://example.com", "act").kind == "browser_open"


def test_shortcut_selection_matches_app_family():
    keycode, flags = shortcut_for("switch_tab", "next", "com.google.Chrome")
    assert keycode == 30 and flags & 0x100000 and flags & 0x20000
    keycode, flags = shortcut_for("switch_tab", "next", "com.apple.Terminal")
    assert keycode == 48 and flags & 0x40000
    assert shortcut_for("switch_to_tab", "4")[0] == 21


def test_open_app_reports_verified_frontmost():
    result = open_app("Notes", opener=lambda _target: True,
                      frontmost=lambda: ("com.apple.Notes", "Notes", 1))
    assert result["ok"] is True and result["verified"] is True


def test_open_app_reports_launch_failure():
    result = open_app("Notes", opener=lambda _target: False,
                      frontmost=lambda: ("", "", 0))
    assert result["ok"] is False


def test_switch_verifies_a_changed_window_title():
    sent = []
    titles = iter(["Old tab", "New tab"])
    result = switch("switch_tab", "next", pid=99, bundle_id="com.google.Chrome",
                    title_fn=lambda _pid: next(titles),
                    dispatch_fn=lambda pid, key, flags: sent.append((pid, key, flags)))
    assert result["ok"] is True and result["verified"] is True
    assert sent == [(99, 30, 0x100000 | 0x20000)]


def test_perform_rejects_non_macos(monkeypatch):
    monkeypatch.setattr("mcp_vision.native_apps.sys.platform", "linux")
    assert perform("open_app", "Notes")["ok"] is False


def test_native_route_executes_with_captured_target(monkeypatch):
    calls = []
    def fake_perform(action, value="", pid=0, bundle_id="", **options):
        calls.append((action, value, pid, bundle_id))
        return {"ok": True, "verified": True, "message": "Opened Notes."}
    monkeypatch.setattr("mcp_vision.native_apps.perform", fake_perform)
    context = Context(source="macos", user_request="Open Notes",
                      accessibility_context={"pid": 4242, "bundle_id": "com.apple.finder"})
    result = asyncio.run(ContextTask(context).run())
    assert result["state"] == "review" and "Notes" in result["answer"]
    assert calls == [("open_app", "Notes", 4242, "com.apple.finder")]

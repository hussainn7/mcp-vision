"""Live Chrome attach, tab routing, AX tree — no browser required."""

import json
from pathlib import Path

from phase2_mcp.ax_tree import box_model_to_rect, flatten_ax_nodes, stamp_badges, union_clip
from phase2_mcp.chrome_bridge import parse_devtools_active_port, permission_banner, websocket_endpoint
from phase2_mcp.tab_router import decide_route, match_tab
from PIL import Image


def test_devtools_active_port_parse(tmp_path: Path):
    (tmp_path / "DevToolsActivePort").write_text("9222\n/devtools/browser/abc\n")
    assert parse_devtools_active_port(tmp_path) == (9222, "/devtools/browser/abc")
    assert websocket_endpoint(tmp_path) == "ws://127.0.0.1:9222/devtools/browser/abc"
    assert parse_devtools_active_port(tmp_path / "missing") is None
    assert "Allow" in permission_banner()


def test_tab_router_reuses_gmail_not_active_tab():
    pages = [
        {"url": "https://github.com/foo", "title": "GitHub"},
        {"url": "https://mail.google.com/mail/u/0/#inbox", "title": "Inbox"},
        {"url": "https://www.amazon.com/", "title": "Amazon"},
    ]
    r = decide_route(pages, "https://mail.google.com", current_index=0)
    assert r.action == "reuse" and r.index == 1
    r = decide_route(pages, "https://news.ycombinator.com", current_index=0)
    assert r.action == "new_tab"
    r = decide_route([{"url": "about:blank", "title": ""}], "https://example.com")
    assert r.action == "navigate"
    assert match_tab(pages, "gmail") == 1


def test_js_blocked_detection():
    from phase2_mcp.chrome_native import _js_blocked, set_forced_apple_events
    assert _js_blocked("execution error: Executing JavaScript through AppleScript is not allowed.")
    assert not _js_blocked("Navigated to https://github.com")
    assert not _js_blocked("ERROR: Chrome is still blocking page access.")
    set_forced_apple_events(True)
    set_forced_apple_events(None)


def test_apple_events_pref_patch(tmp_path: Path):
    from phase2_mcp.chrome_native import _chrome_pref_files, _patch_allow_js_apple_events

    default = tmp_path / "Default"
    default.mkdir()
    prefs = default / "Preferences"
    prefs.write_text('{"browser":{"foo":1},"homepage":"x"}', encoding="utf-8")
    guest = tmp_path / "Guest Profile"
    guest.mkdir()
    (guest / "Preferences").write_text("{}", encoding="utf-8")
    extra = tmp_path / "Profile 2"
    extra.mkdir()
    (extra / "Preferences").write_text("{}", encoding="utf-8")

    files = _chrome_pref_files(tmp_path)
    names = {p.parent.name for p in files}
    assert names == {"Default", "Profile 2"}

    _patch_allow_js_apple_events(prefs)
    data = json.loads(prefs.read_text(encoding="utf-8"))
    assert data["browser"]["allow_javascript_apple_events"] is True
    assert data["account_values"]["browser"]["allow_javascript_apple_events"] is True
    assert data["browser"]["foo"] == 1


def test_ax_flatten_and_badges():
    from phase2_mcp.chrome_native import _parse_tab_lines
    tabs = _parse_tab_lines("1\t1\thttps://github.com/x\tGitHub\n1\t2\thttps://mail.google.com/mail\tInbox\n")
    assert tabs[1]["title"] == "Inbox"
    assert match_tab(tabs, "gmail") == 1
    nodes = [
        {"ignored": True, "role": {"value": "button"}, "name": {"value": "x"}},
        {"role": {"value": "button"}, "name": {"value": "Compose"}, "backendDOMNodeId": 1},
        {"role": {"value": "generic"}, "name": {"value": "nope"}},
        {"role": {"value": "link"}, "name": {"value": "Inbox"}, "backendDOMNodeId": 2},
    ]
    els = flatten_ax_nodes(nodes)
    assert [e["name"] for e in els] == ["Compose", "Inbox"]
    rect = box_model_to_rect({"content": [0, 0, 20, 0, 20, 10, 0, 10]})
    assert rect["w"] == 20 and rect["h"] == 10
    img = Image.new("RGB", (80, 80), (255, 255, 255))
    stamped = stamp_badges(img, [{"index": 0, "cx": 20, "cy": 20, "x": 10, "y": 10, "w": 10, "h": 10}])
    assert stamped.size == (80, 80)
    clip = union_clip([{"x": 10, "y": 10, "w": 20, "h": 10, "cx": 20, "cy": 15}], viewport=(200, 200))
    assert clip and clip["width"] >= 96

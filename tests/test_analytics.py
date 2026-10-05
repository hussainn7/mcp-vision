"""The anonymous daily ping: off when asked, once a day, and only from the app."""
from __future__ import annotations

import json

from click.testing import CliRunner

from mcp_vision import analytics


def test_opt_outs_turn_it_off(monkeypatch):
    for name in ("MCP_VISION_NO_ANALYTICS", "DO_NOT_TRACK"):
        monkeypatch.delenv(name, raising=False)
    assert analytics._disabled() is False
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    assert analytics._disabled() is True
    monkeypatch.setenv("DO_NOT_TRACK", "0")
    monkeypatch.setenv("MCP_VISION_NO_ANALYTICS", "1")
    assert analytics._disabled() is True


def test_one_ping_a_day_with_a_random_install_id(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_VISION_STATE_DIR", str(tmp_path))
    sent = []
    monkeypatch.setattr(analytics, "_post", lambda event, who, props: sent.append((event, who, props)) or True)
    analytics._send("app")
    analytics._send("app")                       # same day: nothing new
    assert len(sent) == 1 and sent[0][0] == "active" and sent[0][2]["command"] == "app"
    saved = json.loads((tmp_path / "analytics.json").read_text())
    assert saved["id"] == sent[0][1] and len(saved["id"]) == 32
    saved["last"] -= 2 * 86400
    (tmp_path / "analytics.json").write_text(json.dumps(saved))
    analytics._send("app")
    assert len(sent) == 2 and sent[1][1] == saved["id"]          # next day, same install


def test_cli_commands_never_ping(monkeypatch):
    from mcp_vision.buddy import cli as buddy_cli

    pinged = []
    monkeypatch.setattr(analytics, "ping", lambda command: pinged.append(command))
    result = CliRunner().invoke(buddy_cli.buddy, ["memory", "show"])
    assert result.exit_code == 0, result.output
    assert pinged == []

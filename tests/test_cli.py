from click.testing import CliRunner

from mcp_vision import cli as cli_module


def test_ui_reuses_existing_contextual_runtime(monkeypatch):
    started = []
    monkeypatch.setattr(cli_module, "_contextual_ui_ready", lambda _port: True)
    monkeypatch.setattr("mcp_vision.macos_ui.run_contextual_ui", lambda **kwargs: started.append(kwargs))

    result = CliRunner().invoke(cli_module.cli, ["ui", "--port", "7332"])

    assert result.exit_code == 0
    assert "already running" in result.output
    assert started == []


def test_ui_starts_when_port_has_no_contextual_runtime(monkeypatch):
    started = []
    monkeypatch.setattr(cli_module, "_contextual_ui_ready", lambda _port: False)
    monkeypatch.setattr("mcp_vision.macos_ui.run_contextual_ui", lambda **kwargs: started.append(kwargs))

    result = CliRunner().invoke(cli_module.cli, ["ui", "--port", "7332", "--model", "local"])

    assert result.exit_code == 0
    assert started and started[0]["port"] == 7332

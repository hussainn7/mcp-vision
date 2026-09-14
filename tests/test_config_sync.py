from __future__ import annotations

import json
import pytest
from pathlib import Path
from types import SimpleNamespace

from mcp_vision.utils import config_sync


@pytest.mark.parametrize("content", ["{broken", "[]", '{"mcpServers": []}', '{"mcpServers": null}'])
def test_bad_config_is_untouched(tmp_path, content):
    path = tmp_path / "config.json"
    path.write_text(content)
    with pytest.raises(ValueError):
        config_sync.install_host("cursor", config_path=path)
    assert path.read_text() == content
    assert not list(tmp_path.glob(".mcp-vision-*"))


def test_selected_host_keeps_other_servers_and_sets_live_options(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "other"}}}))
    config_sync.install_host("antigravity", "/opt/venv/bin/python", config_path=path, allow_writes=True)
    data = json.loads(path.read_text())
    assert data["theme"] == "dark" and data["mcpServers"]["other"] == {"command": "other"}
    assert data["mcpServers"]["mcp-vision"]["args"] == [
        "-m", "mcp_vision.cli", "serve", "--browser", "live", "--driver", "native", "--allow-browser-writes"]


def test_config_cli_and_targeted_install(tmp_path):
    from click.testing import CliRunner
    from mcp_vision.cli import cli
    runner = CliRunner()
    shown = runner.invoke(cli, ["config", "--allow-browser-writes"])
    assert shown.exit_code == 0
    assert "--allow-browser-writes" in json.loads(shown.output)["mcpServers"]["mcp-vision"]["args"]
    target = tmp_path / "cursor.json"
    installed = runner.invoke(cli, ["install", "--host", "cursor", "--config-path", str(target)])
    assert installed.exit_code == 0 and target.exists()
    args = json.loads(target.read_text())["mcpServers"]["mcp-vision"]["args"]
    assert args[args.index("--browser") + 1] == "live"
    assert args[args.index("--driver") + 1] == "native"


def test_merges_without_clobber(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    claude = tmp_path / "claude.json"
    cursor = tmp_path / "mcp.json"
    claude.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    monkeypatch.setattr(config_sync, "claude_config_path", lambda: claude)
    monkeypatch.setattr(config_sync, "cursor_config_path", lambda: cursor)
    monkeypatch.setattr(config_sync.shutil, "which", lambda _name: None)
    paths = config_sync.install_hosts(command="mcp-vision")
    assert claude in paths and cursor in paths
    data = json.loads(claude.read_text())
    assert "other" in data["mcpServers"]
    assert data["mcpServers"]["mcp-vision"]["args"] == ["serve"]
    cur = json.loads(cursor.read_text())
    assert "mcp-vision" in cur["mcpServers"]


def test_registers_codex_with_official_cli(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    claude, cursor, codex = tmp_path / "claude.json", tmp_path / "cursor.json", tmp_path / "config.toml"
    monkeypatch.setattr(config_sync, "claude_config_path", lambda: claude)
    monkeypatch.setattr(config_sync, "cursor_config_path", lambda: cursor)
    monkeypatch.setattr(config_sync, "codex_config_path", lambda: codex)
    monkeypatch.setattr(config_sync.shutil, "which", lambda name: "/bin/codex" if name == "codex" else None)
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1 if "get" in args else 0)
    monkeypatch.setattr(config_sync.subprocess, "run", run)
    paths = config_sync.install_hosts(command="/opt/mcp-vision")
    assert codex in paths
    assert calls[-1] == ["/bin/codex", "mcp", "add", "mcp-vision", "--",
                         "/opt/mcp-vision", "serve"]


def test_registers_claude_code_at_user_scope(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    config = tmp_path / "claude.json"
    monkeypatch.setattr(config_sync, "claude_code_config_path", lambda: config)
    monkeypatch.setattr(config_sync.shutil, "which", lambda name: "/bin/claude" if name == "claude" else None)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1 if "get" in args else 0)

    monkeypatch.setattr(config_sync.subprocess, "run", run)
    assert config_sync._install_claude_code("/opt/mcp-vision") == config
    assert calls[-1] == ["/bin/claude", "mcp", "add", "--scope", "user",
                         "mcp-vision", "--", "/opt/mcp-vision", "serve"]


def test_existing_claude_code_registration_is_left_intact(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(config_sync.shutil, "which", lambda _name: "/bin/claude")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(config_sync.subprocess, "run", run)
    assert config_sync._install_claude_code("/new/path") == config_sync.claude_code_config_path()
    assert len(calls) == 1 and calls[0][2] == "get"


def test_existing_codex_registration_is_left_intact(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(config_sync.shutil, "which", lambda _name: "/bin/codex")
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(config_sync.subprocess, "run", run)
    assert config_sync._install_codex("/new/path") == config_sync.codex_config_path()
    assert len(calls) == 1 and calls[0][2] == "get"

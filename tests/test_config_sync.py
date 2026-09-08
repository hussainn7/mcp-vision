from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from mcp_vision.utils import config_sync


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


def test_existing_codex_registration_is_left_intact(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(config_sync.shutil, "which", lambda _name: "/bin/codex")
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(config_sync.subprocess, "run", run)
    assert config_sync._install_codex("/new/path") == config_sync.codex_config_path()
    assert len(calls) == 1 and calls[0][2] == "get"

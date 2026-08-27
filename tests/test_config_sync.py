from __future__ import annotations

import json
from pathlib import Path

from mcp_vision.utils import config_sync


def test_merges_without_clobber(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    claude = tmp_path / "claude.json"
    cursor = tmp_path / "mcp.json"
    claude.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    monkeypatch.setattr(config_sync, "claude_config_path", lambda: claude)
    monkeypatch.setattr(config_sync, "cursor_config_path", lambda: cursor)
    paths = config_sync.install_hosts(command="mcp-vision")
    assert claude in paths and cursor in paths
    data = json.loads(claude.read_text())
    assert "other" in data["mcpServers"]
    assert data["mcpServers"]["mcp-vision"]["args"] == ["serve"]
    cur = json.loads(cursor.read_text())
    assert "mcp-vision" in cur["mcpServers"]

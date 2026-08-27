"""Write mcp-vision into Claude Desktop and Cursor MCP config files."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from mcp_vision.log import get_logger

log = get_logger("mcp_vision.config_sync")

SERVER_NAME = "mcp-vision"


def _entry(command: str | None = None) -> dict[str, object]:
    cmd = command or shutil.which("mcp-vision") or sys.executable
    if cmd.endswith("python") or cmd.endswith("python3") or "python" in Path(cmd).name:
        return {"command": cmd, "args": ["-m", "mcp_vision.server"]}
    return {"command": cmd, "args": ["serve"]}


def claude_config_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    if sys.platform == "win32":
        app = os.environ.get("APPDATA", str(Path.home()))
        return Path(app) / "Claude/claude_desktop_config.json"
    return Path.home() / ".config/Claude/claude_desktop_config.json"


def cursor_config_path() -> Path:
    return Path.home() / ".cursor" / "mcp.json"


def _merge(path: Path, command: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            log.warning("invalid JSON at %s; starting fresh backup", path)
            path.rename(path.with_suffix(path.suffix + ".bak"))
            data = {}
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        data["mcpServers"] = servers
    servers[SERVER_NAME] = _entry(command)
    path.write_text(json.dumps(data, indent=2) + "\n")
    log.info("wrote %s into %s", SERVER_NAME, path)
    return path


def install_hosts(command: str | None = None) -> list[Path]:
    """Idempotent merge into Claude Desktop + Cursor configs."""
    return [
        _merge(claude_config_path(), command),
        _merge(cursor_config_path(), command),
    ]

"""Register mcp-vision with common local MCP hosts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
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


def codex_config_path() -> Path:
    """Codex CLI, desktop, and IDE clients share this configuration."""
    return Path.home() / ".codex" / "config.toml"


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


def _install_codex(command: str | None = None) -> Path | None:
    """Use Codex's supported CLI so comments and unrelated TOML stay intact."""
    codex = shutil.which("codex")
    if not codex:
        log.info("Codex CLI not found; skipping Codex registration")
        return None
    existing = subprocess.run(
        [codex, "mcp", "get", SERVER_NAME, "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if existing.returncode != 0:
        entry = _entry(command)
        subprocess.run(
            [codex, "mcp", "add", SERVER_NAME, "--", str(entry["command"]),
             *[str(arg) for arg in entry["args"]]],
            capture_output=True,
            text=True,
            check=True,
        )
        log.info("registered %s with Codex", SERVER_NAME)
    return codex_config_path()


def install_hosts(command: str | None = None) -> list[Path]:
    """Idempotently install for Claude Desktop, Cursor, and Codex when present."""
    paths = [
        _merge(claude_config_path(), command),
        _merge(cursor_config_path(), command),
    ]
    codex_path = _install_codex(command)
    if codex_path:
        paths.append(codex_path)
    return paths

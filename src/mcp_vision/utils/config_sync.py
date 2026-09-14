"""Register mcp-vision with common local MCP hosts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp_vision.log import get_logger

log = get_logger("mcp_vision.config_sync")

SERVER_NAME = "mcp-vision"


def _entry(command: str | None = None, *, browser_mode: str | None = None, allow_writes: bool = False,
           live_driver: str | None = None) -> dict[str, object]:
    cmd = command or shutil.which("mcp-vision") or sys.executable
    if cmd.endswith("python") or cmd.endswith("python3") or "python" in Path(cmd).name:
        args = ["-m", "mcp_vision.cli", "serve"]
    else:
        args = ["serve"]
    if browser_mode is not None:
        if browser_mode not in {"live", "isolated"}:
            raise ValueError("browser_mode must be live or isolated")
        args += ["--browser", browser_mode]
        if browser_mode == "live":
            driver = live_driver or "native"
            if driver not in {"native", "cdp"}:
                raise ValueError("live_driver must be native or cdp")
            args += ["--driver", driver]
    if allow_writes:
        args += ["--allow-browser-writes"]
    return {"command": cmd, "args": args}


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


def claude_code_config_path() -> Path:
    return Path.home() / ".claude.json"


def _merge(path: Path, command: str | None = None, *, browser_mode=None, allow_writes=False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {path}; left unchanged. Repair it before installing.") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}; left unchanged.")
    servers = data.get("mcpServers")
    if "mcpServers" not in data:
        servers = {}
        data["mcpServers"] = servers
    elif not isinstance(servers, dict):
        raise ValueError(f"mcpServers must be an object in {path}; left unchanged.")
    servers[SERVER_NAME] = _entry(command, browser_mode=browser_mode, allow_writes=allow_writes)
    content = json.dumps(data, indent=2) + "\n"
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".mcp-vision-", delete=False) as out:
            temp = Path(out.name)
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        if path.exists():
            temp.chmod(path.stat().st_mode & 0o777)
        temp.replace(path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    log.info("wrote %s into %s", SERVER_NAME, path)
    return path


def install_host(host: str, command: str | None = None, *, browser_mode="live", allow_writes=False,
                 config_path: Path | None = None) -> Path:
    """Only update the host selected by the operator."""
    paths = {"cursor": cursor_config_path, "claude-desktop": claude_config_path,
             "antigravity": lambda: Path.home() / ".gemini/config/mcp_config.json"}
    if host not in paths:
        raise ValueError("Choose cursor, claude-desktop, or antigravity.")
    path = config_path if config_path is not None else paths[host]()
    return _merge(path, command, browser_mode=browser_mode, allow_writes=allow_writes)


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


def _install_claude_code(command: str | None = None) -> Path | None:
    """Register the server at user scope when Claude Code is installed."""
    claude = shutil.which("claude")
    if not claude:
        log.info("Claude Code CLI not found; skipping Claude Code registration")
        return None
    existing = subprocess.run(
        [claude, "mcp", "get", SERVER_NAME],
        capture_output=True,
        text=True,
        check=False,
    )
    if existing.returncode != 0:
        entry = _entry(command)
        subprocess.run(
            [claude, "mcp", "add", "--scope", "user", SERVER_NAME, "--",
             str(entry["command"]), *[str(arg) for arg in entry["args"]]],
            capture_output=True,
            text=True,
            check=True,
        )
        log.info("registered %s with Claude Code", SERVER_NAME)
    return claude_code_config_path()


def install_hosts(command: str | None = None) -> list[Path]:
    """Idempotently install for Claude, Cursor, and Codex when present."""
    paths = [
        _merge(claude_config_path(), command),
        _merge(cursor_config_path(), command),
    ]
    claude_code_path = _install_claude_code(command)
    if claude_code_path:
        paths.append(claude_code_path)
    codex_path = _install_codex(command)
    if codex_path:
        paths.append(codex_path)
    return paths

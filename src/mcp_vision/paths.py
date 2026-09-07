"""Runtime state never belongs inside the installed package."""
import os
from pathlib import Path


def state_dir() -> Path:
    return Path(os.environ.get("MCP_VISION_STATE_DIR", Path.home() / ".local/share/mcp-vision"))

"""Stderr-only logging. stdout is reserved for MCP JSON-RPC."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG = logging.getLogger("mcp_vision")
_CONFIGURED = False


def get_logger(name: str | None = None) -> logging.Logger:
    configure()
    return logging.getLogger(name or "mcp_vision")


def configure(log_file: Path | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    _LOG.setLevel(logging.INFO)
    _LOG.handlers.clear()
    _LOG.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    err = logging.StreamHandler(sys.stderr)
    err.setFormatter(fmt)
    _LOG.addHandler(err)
    path = log_file or Path.home() / ".mcp-vision" / "mcp-vision.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3)
        fh.setFormatter(fmt)
        _LOG.addHandler(fh)
    except OSError:
        pass
    _CONFIGURED = True

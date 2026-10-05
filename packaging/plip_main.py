"""Entry point for the bundled Plip.app (PyInstaller)."""
import sys

from mcp_vision.cli import cli

if __name__ == "__main__":
    sys.exit(cli(["buddy", "run"], prog_name="plip"))

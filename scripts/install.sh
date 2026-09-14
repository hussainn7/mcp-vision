#!/usr/bin/env bash
# One-liner install for mcp-vision (macOS / Linux).
# curl -fsSL https://raw.githubusercontent.com/hussainn7/mcp-vision/main/scripts/install.sh | bash
set -euo pipefail

REPO="${MCP_VISION_REPO:-https://github.com/hussainn7/mcp-vision.git}"
HOST="${MCP_VISION_HOST:-cursor}"

echo "==> installing mcp-vision"
python3 -m pip install --upgrade "pip" >/dev/null
python3 -m pip install "git+${REPO}"

echo "==> chromium (for isolated demos)"
python3 -m playwright install chromium || true

echo "==> wiring ${HOST}"
if command -v mcp-vision >/dev/null 2>&1; then
  mcp-vision setup --host "$HOST" || mcp-vision install --host "$HOST" --allow-browser-writes
else
  python3 -m mcp_vision.cli setup --host "$HOST" || python3 -m mcp_vision.cli install --host "$HOST" --allow-browser-writes
fi

echo
echo "Done."
echo "  mcp-vision connect"
echo "  mcp-vision ask \"flights to SFO next weekend\""
echo "  (restart Cursor / Claude so MCP picks it up)"

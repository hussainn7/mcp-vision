#!/usr/bin/env bash
# curl -fsSL https://raw.githubusercontent.com/hussainn7/mcp-vision/main/scripts/install.sh | bash
# Always uses Python >= 3.12 (macOS system python3 is often 3.9 — that will fail).
set -euo pipefail

REPO="${MCP_VISION_REPO:-https://github.com/hussainn7/mcp-vision.git}"
HOST="${MCP_VISION_HOST:-cursor}"
PY="${MCP_VISION_PYTHON:-3.12}"

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

echo "==> mcp-vision install (Python ${PY}+ required)"

if ! command -v uv >/dev/null 2>&1; then
  echo "==> installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "Could not install uv. Install Python 3.12 then:"
  echo "  brew install python@3.12"
  echo "  python3.12 -m pip install \"git+${REPO}\""
  exit 1
fi

echo "==> ensuring Python ${PY}"
uv python install "$PY" >/dev/null

echo "==> installing mcp-vision"
uv tool install --force --python "$PY" "git+${REPO}"

export PATH="$HOME/.local/bin:$PATH"
hash -r 2>/dev/null || true

echo "==> playwright chromium"
uv tool run --from playwright playwright install chromium 2>/dev/null \
  || "$HOME/.local/share/uv/tools/mcp-vision-runtime/bin/python" -m playwright install chromium 2>/dev/null \
  || true

echo "==> wiring ${HOST}"
mcp-vision setup --host "$HOST" --skip-playwright \
  || mcp-vision install --host "$HOST" --allow-browser-writes

echo
echo "Done."
if ! command -v mcp-vision >/dev/null 2>&1; then
  echo "Add to your shell config, then reopen the terminal:"
  echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi
echo "  mcp-vision connect"
echo "  mcp-vision ask \"flights to SFO next weekend\""
echo "  (restart Cursor / Claude so MCP picks it up)"

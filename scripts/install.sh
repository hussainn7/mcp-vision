#!/usr/bin/env bash
# The `plip` command from source, for people who'd rather not use the app:
#   curl -fsSL https://raw.githubusercontent.com/hussainn7/plip-oss/main/scripts/install.sh | bash
# Most people want the DMG instead (Releases on GitHub). Uses Python 3.12 (macOS's own python3 is too old).
set -euo pipefail

REPO="${PLIP_REPO:-https://github.com/hussainn7/plip-oss.git}"
PY="${PLIP_PYTHON:-3.12}"

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo "==> installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "Couldn't install uv. Install Python 3.12 (brew install python@3.12), then:"
  echo "  python3.12 -m pip install \"git+${REPO}\""
  exit 1
fi

echo "==> Python ${PY}"
uv python install "$PY" >/dev/null
echo "==> installing plip"
uv tool install --force --python "$PY" "git+${REPO}"
hash -r 2>/dev/null || true

echo
echo "Done."
if ! command -v plip >/dev/null 2>&1; then
  echo "Add this to your shell config, then reopen the terminal:"
  echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi
echo "  plip          # opens Settings on first run; pick the Claude / ChatGPT / Cursor / Gemini plan you have"
echo "  plip doctor   # checks brains, keys and permissions"
echo "Then hold Control+Option and talk."

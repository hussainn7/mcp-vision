# MCP-Vision

Computer use for agents — your Chrome, your model.

Needs **Python 3.12+** (macOS `/usr/bin/python3` is often 3.9 and will fail).

## Install (one liner)

```bash
curl -fsSL https://raw.githubusercontent.com/hussainn7/mcp-vision/main/scripts/install.sh | bash
```

That script installs [`uv`](https://github.com/astral-sh/uv) if needed, fetches Python 3.12,
and puts `mcp-vision` on `~/.local/bin`.

### Manual

```bash
# if you don't have 3.12 yet:
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.12

uv tool install "git+https://github.com/hussainn7/mcp-vision.git" --python 3.12
export PATH="$HOME/.local/bin:$PATH"
mcp-vision setup
```

Do **not** use stock `pip3` on macOS if it reports 3.9.

### Devs (clone)

```bash
cd mcp-vision
python3.12 -m venv .venv   # or: uv venv --python 3.12
source .venv/bin/activate
pip install -e .
mcp-vision setup
```

Then open Chrome and refresh MCP in Cursor / Claude.

## Ask the bot

```bash
mcp-vision ask "flights to SFO from ATL Sept 28 to Oct 2"
mcp-vision ask "what's on my gmail"
mcp-vision ask "mechanical keyboard on ebay under 100"
```

Uses your existing Chrome (native, no automation banner). On success a second model
pass (Ollama by default) cleans the raw page into a short answer. Use `--model none`
for a local heuristic only.

## Connect an agent host

```bash
mcp-vision setup --host cursor
mcp-vision setup --host claude-desktop
mcp-vision setup --host antigravity
```

Or print config: `mcp-vision config --allow-browser-writes`

Claude Code:

```bash
claude mcp add --transport stdio mcp-vision -- mcp-vision serve --browser live --driver native --allow-browser-writes
```

Then ask the host in plain English: *list my tabs and find flights to SF…*

## How live Chrome works

`serve --browser live` (default `--driver native`) drives the Chrome you already
have open — cookies, extensions, tabs. No remote-debugging toggle. No “controlled
by automated software” banner.

CAPTCHA? You get a notification — solve it in Chrome, click **I solved it**.
Buy / send / book still need Allow once.

## Quick checks

```bash
mcp-vision connect
mcp-vision demo
mcp-vision probe --live
mcp-vision studio
```

## License

MIT

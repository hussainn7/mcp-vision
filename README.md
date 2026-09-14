# MCP-Vision

Computer use for agents — your Chrome, your model.

## Install (one liner)

```bash
curl -fsSL https://raw.githubusercontent.com/hussainn7/mcp-vision/main/scripts/install.sh | bash
```

Or:

```bash
pip install "git+https://github.com/hussainn7/mcp-vision.git"
mcp-vision setup
```

Devs working from a clone:

```bash
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

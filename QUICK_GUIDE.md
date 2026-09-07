# Quick guide

Start with the [README](README.md). The supported development focus is the model-neutral MCP browser runtime; the bundled autonomous agent and personal-profile bridges are experimental.

## Isolated browser

Install the checkout and Chromium, then run `mcp-vision demo` on the intended executor. The demo needs no model or credentials. Configure your MCP host to launch `mcp-vision serve` using an absolute executable path. Add `--allow-browser-writes` only when the operator wants routine input; recognized sensitive actions still need confirmation. On a headless executor, those actions block without a confirmer.

Use `browser_navigate`, `browser_snapshot`, then `browser_fill` or `browser_click` with the returned snapshot ID and index. Take a new snapshot after input. Verify a relevant postcondition instead of interpreting a dispatched click as task success. See [the runtime contract](docs/RUNTIME.md).

## Desktop preview

`inspect_screen` returns image-derived regions, optionally labeled with OCR. `screen_image` returns pixels to the host. Desktop input always requires human confirmation and a fresh unchanged observation for targeted actions. Install `.[hud]` for the optional overlay. Validate native input and permissions on the intended OS before use.

## Legacy example agent

`mac-agent` / `agent.py` includes specialist prompts, local/cloud backend adapters, and a Plan–Act–Reflect loop. This is compatibility code with a separate execution path from the new isolated MCP browser. It uses the configured provider and can interact with personal applications. Review its tools before use.

Heuristic judge scores describe execution quality, not verified task completion. Automatic skill learning requires a trusted `completion_check` callback supplied by the application. By default completion is unknown. Runtime state defaults beneath `~/.local/share/mcp-vision`; `MCP_VISION_STATE_DIR` overrides this base. Legacy `SCREEN_AGENT_*` settings can override specific paths and backend configuration.

Test commands and hosted CI are documented in [CONTRIBUTING.md](CONTRIBUTING.md). New runtime changes remain unvalidated until the hosted checks pass.

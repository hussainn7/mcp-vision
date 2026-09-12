# Mission Control direction

Build a local action layer for the agent people already use. The host owns the
model and planning; MCP-Vision owns observations, grounded actions, and receipts.
Mission Control makes the first useful task easy to prepare and inspect.

The current slice includes four editable recipes, portable task briefs, host
configuration examples, session activity, and a real browser demo. Briefs are
explicitly marked as unexecuted. The demo is deterministic, not an AI planner.

Next priority: validate a user-selected task inside a real MCP host, then improve
recovery from failures based on that evidence. Avoid adding another general
planner, model subscription, or cloud service before that path is proven useful.

## Local validation, September 12, 2026

- Full Python suite: 107 passed before the additional studio HTTP tests.
- Chrome computer use: inspected the workspace, ran the browser demo, loaded a
  checkup recipe, prepared a brief, inspected its full prompt, and switched the
  connection instructions from Cursor to Claude Code with routine input enabled.
- The demo returned all six expected receipts and passed its final checks.
- Browser tests require execution outside the desktop filesystem sandbox on this
  Mac; no browser security settings were changed.
- Native desktop automation, a cloud/local-model host comparison, and hosted CI
  are not validated by these checks. Ollama was unavailable during this session.

Run `mcp-vision studio` to review the workspace. No external host configuration
was installed or modified during UI testing.

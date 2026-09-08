# Contributing

Changes should improve a demonstrated failure, not just increase the tool count. Include a reproducible fixture, expected postcondition, and whether input was dispatched. Keep model evaluation separate from deterministic runtime checks.

## Test on a disposable server

Use Python 3.12 or 3.13 on an Ubuntu test executor:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install '.[dev]' build
python -m playwright install --with-deps chromium
export PYTHONPATH=src:.
export MCP_VISION_STATE_DIR="$(mktemp -d)"
export MCP_VISION_HUD=off
export MCP_VISION_BROWSER_TESTS=1
xvfb-run -a python -m pytest
mcp-vision demo
xvfb-run -a python tests/run.py
xvfb-run -a python bench/runner.py
xvfb-run -a python tests/e2e_web.py
MCP_VISION_BROWSER_TESTS=1 xvfb-run -a python tests/live_web_smoke.py
python -m build
```

Install `xvfb`/`xauth` if missing. These checks require no paid API, local model inference, or personal profile. GitHub Actions runs this sequence plus installation and MCP protocol checks from outside the checkout. Its artifacts preserve benchmark reports and wheels. New code is not validated until those jobs pass.

Desktop tests use synthetic images and a recording actuator. They do not prove real macOS, Windows, or Linux input behavior. Real OS checks belong on an explicitly designated disposable executor with its own test accounts and permissions.

`tests/live_web_smoke.py` makes read-only requests to IANA's stable `example.com` page. It proves public navigation, observation, evidence, and screenshots on the hosted runner. It does not test authenticated sites, transactions, or a particular model provider.

For a new bug, prefer a small regression test: replacement DOM nodes, duplicate labels, late overlays, navigation during approval, timeouts after dispatch, wrong form values, or missing postconditions. A skipped browser test is not evidence of browser correctness.

## Scope and release gate

The current focus is a model-neutral runtime with explicit evidence. The native Chrome relay and bundled provider-backed agents are experimental. Do not add another agent framework before strengthening runtime contracts.

Before a release: all hosted checks must pass, install from the built wheel in a clean environment, verify a real MCP host connects, and document OS/browser/model configurations actually tested. Do not publish a broad reliability percentage from a small synthetic suite.

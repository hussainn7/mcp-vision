# Contributing

Plip is the Python package in `src/mcp_vision/buddy/` plus a React UI in `ui/` that
builds into one file, `src/mcp_vision/buddy/web/index.html`.

## Checks

Every change passes the same checks CI runs:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
ruff check . && python -m pytest
cd ui && npm ci && npx playwright install chromium
npm run typecheck && npm run build && npm run e2e
```

The Python tests need no model, network or display: the CLI brains run as fake
subprocesses, and a full `plip` CLI run goes against a fake Claude Code. They run in a
throwaway state folder, so they never touch your real Plip. Commit the rebuilt
`web/index.html` with any UI change.

For a bug, add a small test that fails before the fix.

## On a real Mac

Changes to the macOS layer (`*_macos.py`, `hotkey.py`, `ax_locator.py`) also need the
manual checklist in [docs/BUDDY.md](docs/BUDDY.md#manual-check-on-a-mac). Write what you
checked in the pull request.

## Releases

See [docs/RELEASING.md](docs/RELEASING.md).

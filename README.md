# MCP-Vision

An open-source contextual action layer for your computer. Point at what you're
working on, invoke MCP-Vision, and let your preferred model Ask, Guide, or Act—with
verification and evidence for meaningful actions.

**Your model. Your computer. Evidence for every action.**

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

## Contextual invocation on macOS

```bash
mcp-vision doctor
mcp-vision ui
```

Tap **Option-Space** anywhere to open the MCP-Vision text panel. Hold
**Option-Space** to speak: a compact top-center panel streams the transcript,
and releasing the shortcut submits the final text. It stays compact through
understanding, acting, verification, and normal completion; clarification and
errors open the detailed panel. The app collects the foreground application,
window, selection, and focused accessibility element when macOS makes those
fields reliably available. A simple Ask is answered in place; the inferred
Ask · Guide · Act labels do not create a second automation engine.

The first voice interaction asks for Microphone and Speech Recognition access.
macOS chooses on-device recognition when available and may use Apple Speech as
a fallback. Audio is not saved. Privacy-safe latency milestones are written to
`~/.local/share/mcp-vision/interaction_metrics.jsonl` without transcript text.

Auto chooses the behavior separately for each request. Ask can answer general
questions or gather read-only browser evidence; Guide points at controls; Act
performs supported operations and checks the resulting state. The popup retains
the app/tab captured at invocation instead of capturing its own Go button.

Examples in the popup:

- “Explain what a heat pump does” → direct answer.
- “Research heat pumps” → Google search and observed evidence.
- “What unread emails are in my Gmail?” → existing Gmail session; sign-in is
  requested only if needed.
- “Find flights to SFO next week” → asks for the missing departure airport;
  your next reply continues that request.
- “Open Gmail” → opens or reuses the service and verifies the destination.
- “Open the Notes app” → launches Notes and verifies that it is frontmost.
- “Where is the export button?” → Guide on the captured app.
- “Fill this using my résumé, don’t submit” → factual filling and review.

The runtime checks the selected model before browser research or model-driven
operations and can start an installed Ollama app if it is stopped. Missing models,
credentials, OS permissions, sign-in and CAPTCHA remain explicit setup/user steps.
It does not silently change providers or download model weights. This is a bounded
assistant, not universal automation: unsupported controls and uncertain outcomes
stop with a blocker; sending, booking and purchasing remain gated.

While a task is running, hold **Option-Space** and start speaking to interrupt it
at the next safe boundary and replace it with the new request. Partial speech is
shown mid-sentence; actions still wait for a final transcript to avoid executing
an incomplete command.

For Chrome, open `chrome://extensions`, enable Developer mode, choose **Load
unpacked**, and select this repository's `chrome_relay` folder. Right-click a page
and choose **Ask MCP-Vision**. The action sends a bounded selection/element/nearby
DOM context to the same local runtime and opens the native popup. Keep
`mcp-vision ui` running while using the action.

Useful checks:

```bash
mcp-vision status
mcp-vision install --host cursor
```

The UI is optional. Existing `mcp-vision serve`, MCP host configuration, and CLI
workflows remain independent.

### Runtime boundary

The core owns context, orchestration, trust decisions, verification, receipts,
and UX. Browser and desktop control sit behind an execution-backend protocol;
the existing native/CDP/isolated runtimes are the defaults and are not duplicated.

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
mcp-vision bench-fastpath --iterations 10
mcp-vision probe --live
mcp-vision studio
mcp-vision status
```

## Reasoning harness

`src/mcp_vision/reasoning/` is a general, runtime-agnostic decision-making layer
(not a workflow system): it turns vague requests into a persistent loop of
understand → assume defensibly → investigate → verify → re-evaluate → finish,
while the runtimes keep ownership of reality and safety. The model owns
judgment; the runtime owns real execution, permissions, and verification.
See [docs/REASONING_HARNESS.md](docs/REASONING_HARNESS.md).

## State-scoped actions

The bounded runtime path exposes immutable UI states, state-owned `@e` element
references, compiled action candidates, optional fast-policy selection, and
single-action transactions with successor diffs and semantic postconditions.
See [docs/STATE_RUNTIME.md](docs/STATE_RUNTIME.md).

The bounded FastPath can execute a routine multi-step subgoal with stale/no-op/
loop budgets and evidence-based completion. Reproduce the local dynamic-browser
comparison in [docs/BENCHMARKS.md](docs/BENCHMARKS.md); the checked-in report is
deliberately explicit about what was and was not measured.

For the execution ladder, perception fallback, Studio/replay experience,
dogfooding evidence, and current limitations, see
[docs/COMPUTER_USE_RUNTIME.md](docs/COMPUTER_USE_RUNTIME.md).

## License

MIT

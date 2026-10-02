# MCP-Vision

An AI buddy that lives next to your cursor. Hold **Control+Option**, ask out loud,
and it looks at your screen, answers in a natural voice, and **flies a little blue
pointer to the exact button, menu, or field** you need.

> "where do I change the export resolution?"
> *"open the file menu up top"* → the pointer arcs over to **File** →
> *"then pick export, and the resolution is in the dialog that opens."*

It is a Python take on [Clicky](https://github.com/farzaa/clicky). It follows
Clicky's prompt, pointing protocol, motion design and push-to-talk feel, and adds
a few things on top:

| | Clicky (open source) | MCP-Vision buddy |
|---|---|---|
| Speech starts | after the whole reply is generated and synthesized | per sentence, while Claude is still streaming |
| Pointing | one point, at the end | one point per step of a walkthrough, synced to the sentence |
| Pointer lands on | the model's estimate | the real control, snapped via Accessibility (Jev picks the element) |
| Screenshots | always, every screen | Jev decides in ~100 ms: none for general questions, cursor screen only when that's all that matters |
| Effort | fixed | Jev spots walkthroughs and raises Claude's effort for that turn |
| Bad tags | read aloud | never spoken |

Needs **Python 3.12+** and macOS for the buddy overlay. The headless `buddy ask`
and the MCP server run anywhere.

## Quick start

```bash
curl -fsSL https://raw.githubusercontent.com/hussainn7/mcp-vision/main/scripts/install.sh | bash
mcp-vision buddy setup        # paste your Anthropic key; Jev, ElevenLabs, AssemblyAI are optional
mcp-vision buddy doctor       # add --ping to verify the Jev key
mcp-vision buddy
```

The first run asks macOS for **Screen Recording**, **Accessibility** (for the
hotkey and pointer snapping) and **Microphone**. For permissions that stick across
updates, build the signed app once: `python scripts/build_macos_app.py`, then open
`/Applications/MCP-Vision.app`.

Then hold **Control+Option**, talk, and let go.

- Press the shortcut again while it is talking to interrupt (barge-in).
- Press any other key during the chord and it treats it as some other shortcut.
- The menu-bar triangle has *Forget this conversation*, *Check setup*, and
  *Always show buddy*.

### What each key buys you

| Key | Without it | With it |
|---|---|---|
| `ANTHROPIC_API_KEY` | required | Claude (`claude-opus-5-5`, low effort by default) sees your screens and writes the reply |
| `TYPESAFE_API_KEY` | rule-based routing, heuristic snapping | [Jev](https://console.typesafe.ai) System-1 routing and element choice in ~100 ms |
| `ELEVENLABS_API_KEY` | macOS `say` | Clicky's ElevenLabs voice (`eleven_flash_v2_5`) |
| `ASSEMBLYAI_API_KEY` | on-device Apple Speech | AssemblyAI streaming (`u3-rt-pro`), as in Clicky |

Every knob is in [.env.example](.env.example).

### Try it without a Mac

```bash
mcp-vision buddy ask --image screenshot.png "where's the export button?"
mcp-vision buddy ask --json "what's on my screen?"      # captures the real screen if there is one
```

This prints what the buddy would say, where it would point (global screen points),
the route Jev or the rules chose, and the latency of each stage.

## How it works

```
Control+Option ──▶ mic ──▶ AssemblyAI / Apple Speech ──▶ transcript
                                                             │
   release: screenshots prefetched ──┐                       ▼
                                     ├──▶ Jev router (needs screen? which monitor? walkthrough?)
                                     ▼
          Claude (streamed) ──▶ sentence splitter ──▶ TTS queue (speaks sentence 1 while 2 is synthesized)
                                     │
                                     └──▶ [POINT:x,y:label:screenN] ──▶ map px → screen points
                                                                      ──▶ snap to AX element (Jev picks)
                                                                      ──▶ buddy flies there, types the label
```

Everything above the overlay is platform-neutral and unit-tested. The macOS layer
is a 60 Hz renderer, an event tap and audio glue. See [docs/BUDDY.md](docs/BUDDY.md).

## MCP server (for Cursor, Claude Desktop, Claude Code)

The original browser/desktop runtime still ships, and it is independent of the buddy:

```bash
mcp-vision setup --host cursor               # or claude-desktop, antigravity
claude mcp add --transport stdio mcp-vision -- mcp-vision serve --browser live --driver native --allow-browser-writes
```

- `serve --browser live` drives the Chrome you already have open, with no
  automation banner.
- With `TYPESAFE_API_KEY` set, routine actions are picked by Jev's bounded
  fast policy. Consequential actions always escalate.
- Buy, send and book actions still need *Allow once*.

More: [docs/RUNTIME.md](docs/RUNTIME.md), [docs/STATE_RUNTIME.md](docs/STATE_RUNTIME.md),
[docs/BENCHMARKS.md](docs/BENCHMARKS.md).

The previous Option-Space popup (Ask / Guide / Act with form filling) is still
available as `mcp-vision ui`, or as an app with `scripts/build_macos_app.py --entry ui`.

## Development

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
PYTHONPATH=src:. python -m pytest
```

The CI sequence, including real Chromium contracts, is in
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT

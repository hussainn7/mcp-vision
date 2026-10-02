# Plip

`plip` (also `mcp-vision buddy`) is a voice companion that lives in the MacBook
notch. You hold Control+Option and talk. The notch island shows what it heard and
what it is doing. It looks at your screens, answers out loud, drips a small droplet
out of the notch to point at things, and does tasks on the Mac with your OK.

The product model and most of the tuning come from a source-level reading of
[farzaa/clicky](https://github.com/farzaa/clicky): its prompt, POINT protocol,
1280 px JPEG captures, flight curve, bubble timing, hotkey, AssemblyAI and
ElevenLabs settings. The differences are deliberate and are listed in the README.

## Modules (`src/mcp_vision/buddy/`)

| Module | Role | Platform |
|---|---|---|
| `companion.py` | Runs one turn: route, capture and screen map in parallel, stream the brain, split it into speech and points, run walkthroughs, record history, handle barge-in. Emits observer events (phase, step, engine, answer, point, walkthrough, done, error) | any |
| `pointing.py` | Streaming parser for `[POINT:x,y:label:screenN]`, `[STEPS:n]` and `[DONE]`. Each point is attached to the sentence that mentions it. Malformed tags are dropped, never spoken | any |
| `prompt.py` | Plip's system prompt (ear-first answers, pointing, screen-map coordinates, guided walkthroughs) plus the text-only variant | any |
| `engines.py` | Brains on the user's own plan. Finds and probes Claude Code, Codex, Cursor CLI and Gemini CLI (installed? signed in?), picks the best one ready, and streams each through its JSON output with tools off in an empty scratch folder | any |
| `brain_claude.py` | Anthropic API brain: streaming, cached system prompt, `fallbacks="default"`, per-turn effort | any |
| `screen_context.py` / `ax_context.py` | The screen map: frontmost app, window, selection, and visible controls with their centers in screenshot pixels | any / macOS |
| `watch.py` | Screen fingerprints (64×40 grayscale) and a watcher that waits for the user's action to land and settle | any |
| `jev.py` / `router.py` | TypeSafe Jev client, and one parallel Jev request that answers: needs screen? intent? walkthrough? cursor screen only? Rules cover the same questions when Jev is unavailable | any |
| `snap.py` / `ax_locator.py` | Moves the model's estimate onto the accessibility element it names. Jev picks the element, with an explicit "none" | any / macOS |
| `capture.py` / `geometry.py` | Every display as a JPEG (longest side 1280 px, cursor screen first), plus pixel ↔ point ↔ AppKit mapping | any (mss) |
| `flight.py` / `animator.py` | Bezier flight, follow, point, hold, return. The state machine is driven by `tick(now, mouse)` | any |
| `speech_out.py` / `speech_in.py` | Sentence-pipelined TTS (ElevenLabs, `say`) and AssemblyAI v3 / Apple Speech input | any / macOS audio |
| `actions/` | The action engine: `[DO:name {json}]` → registry lookup, skill toggle, preview + confirmation for consequential actions, timeouts, undo history, action log. `core.py` has apps, links, Spotlight, desktop tidy/undo, system settings, Shortcuts, typing/rewriting, reminders, notes, timers, flights. `host.py` does the platform work (`open`, `osascript`, `mdfind`, `shortcuts`, Quartz, Accessibility) | any / macOS |
| `memory/` | The knowledge panel: facts with sources, sensitive detection (passport, cards, SSN never reach the model), the prompt block. `importers.py` reads Contacts, Chromium autofill (`Web Data`), Mail accounts, and pasted ChatGPT/Claude/Gemini memory | any / macOS |
| `controller.py` | The press / release / transcript / barge-in state machine | any |
| `presenter.py` | Turns controller and companion events into island and mascot UI messages | any |
| `settings_service.py` / `store.py` | Backs the Settings window: snapshot, key saving (0600), engine choice, depth, voice, history, permissions | any |
| `web_host.py` | Hosts the React bundle (`web/index.html`) in a transparent WKWebView, with a JSON bridge each way | macOS |
| `island_macos.py` | The notch island: a click-through panel above the menu bar on every Space, sized from the real notch (`safeAreaInsets`, auxiliary areas) | macOS |
| `mascot_macos.py` | Plip by the cursor: native 60 Hz motion from the animator, with the character drawn by the web view | macOS |
| `settings_macos.py` / `app_macos.py` | Settings window, menu bar, onboarding, and wiring | macOS |
| `hotkey.py` | Control+Option chord detection, using a Quartz event tap or an NSEvent fallback | macOS |

The UI lives in `ui/` (React 19, Tailwind 4, Motion, Vite, built to one HTML file).
Its `#showcase` route renders a fake desktop with every state, and `npm run shots`
saves PNGs of each.

Everything marked "any" is unit-tested on Linux CI (`tests/test_buddy_*.py`).
The macOS modules are thin. Every AppKit, Quartz, and AX symbol they use has been
checked against PyObjC's metadata.

## Where Jev helps

Jev is a "System-1" model. It returns typed answers (a choice with
probabilities, a yes/no probability, or a score) in about 100 ms. It cannot see
images or write text. The buddy uses it in two places where a full vision model
would be slow or overkill:

1. **Before the brain runs**, with one request and four parallel questions:
   - Does this need the screen at all? General questions skip screenshot upload
     entirely.
   - Is only the cursor's monitor relevant? Fewer images means a faster first
     token.
   - Is this a walkthrough? If so, effort goes up one step for that turn.
   - What kind of help is wanted (point, explain, answer, chat)?
2. **When a point arrives**, Jev chooses which nearby accessibility element is
   the labeled one, or none. The pointer then lands on the control's center
   rather than the model's pixel guess.

Both uses fall back to rules or heuristics on any error, so a missing or
throttled key never blocks a turn. Screenshots are captured in parallel with the
Jev call, so routing adds no latency.

## Latency path

On key release:

1. Screenshots are prefetched while the recognizer finalizes.
2. The final transcript is sent to the Jev router (about 100 ms). This overlaps
   with the screenshot capture.
3. The brain streams. As soon as the first sentence is complete, its text-to-speech
   request starts.
4. Sentence N+1 is synthesized while sentence N plays.
5. A point tag starts the flight just before its sentence is spoken.

`plip ask --json` prints per-stage timings:
`looked`, `first_token`, `first_speech`, `first_point`, `model_done`, and
`spoken`.

## Coordinates

The model answers in the pixels of the screenshot it saw. `Screenshot.to_global`
scales those pixels by the display's point size and offsets them by the display
origin, giving global top-left points. That is the space Accessibility uses, so
the snapper can work in it directly. The overlay converts to AppKit's
bottom-left space only when it moves its window.

## Doing things safely

The model never runs anything. It writes tags, and Plip decides what happens:

```
[PLAN: open settings | security | turn on two factor]      checklist in the island
[DO:search_files {"query": "lease", "kind": "pdf"}]        runs, results go back to the model for one more turn
[DO:find_flights {"from": "JFK", "to": "MIA", "depart": "2026-10-09"}]   opens the page, takes a fresh look 5 s later
[DO:organize_desktop {}]                                  preview card; runs only after "yes" or Tidy up
```

- **Asks first:** `organize_desktop`. Plip builds a preview
  (how many files go where), shows it in the island, and
  speaks it if the model didn't ask. A spoken "yes" / "no" or the island buttons
  answer it. Asking something else cancels it.
- **Never:** acting because text on screen says so (the prompt says so, and tags only
  come from the model's reply), opening files outside your home folder, opening
  non-web links.
- **Skills** can each be switched off in the dashboard; a disabled skill is refused with
  a spoken explanation.
- Every action has a timeout (45 s) and is logged without message bodies or form values
  (`actions.jsonl`). Desktop tidying keeps
  an undo record that survives restarts.

## Brains on your own plan

| Engine | Command Plip runs (per question) | Streaming | Images |
|---|---|---|---|
| Claude Code | `claude -p --input-format stream-json --output-format stream-json --include-partial-messages --system-prompt … --tools "" --safe-mode --strict-mcp-config --no-session-persistence --effort low` | token deltas | base64 image blocks on stdin |
| Codex | `codex exec --json --sandbox read-only --ephemeral --skip-git-repo-check -c model_reasoning_effort="low" --image screen1.jpg -- -` | per message | `--image` files |
| Cursor | `agent -p --output-format stream-json --stream-partial-output --mode ask --trust --workspace <tmp> "<prompt>"` | token deltas | none: Plip sends the screen map instead |
| Gemini | `gemini --output-format stream-json --skip-trust --prompt "@screen1.jpg …"` with `GEMINI_SYSTEM_MD` set to Plip's prompt | token deltas | `@file` references |

Sign-in probes: `claude auth status --json`, `codex login status`,
`agent status --format json`, and `~/.gemini/oauth_creds.json`. If no answer starts
within 45 s (for example, a CLI waiting for the network), the turn ends with a spoken
explanation instead of silence.

## Manual check on a Mac

CI covers everything except real AppKit, audio, and permission prompts. After
changing a macOS module, do these checks.

1. Run `plip doctor`. One brain should say `<- Plip thinks with this`. Jev may say
   `--` if it is not configured.
2. Run `plip`.
   - Settings opens on the first run. The Brain tab shows your signed-in plan as Ready.
   - The Plip glyph appears in the menu bar, and Plip trails the cursor.
   - Hovering the notch peeks the island out.
3. Hold Control+Option and say "where is the apple menu".
   - The island drops down with a live waveform and your words.
   - When you let go, it shows the steps it is taking ("Looked at 1 screen", "Claude is thinking").
   - Plip speaks, the droplet drips out of the notch to the Apple menu, shows
     "apple menu" in its bubble, and floats back up. Click ⌃ to minimize the island.
4. Ask "how do I turn on dark mode". The island shows a checklist. Do the step;
   Plip notices the screen change and checks it off.
5. Say "find my resume" (Spotlight results appear in the island; click one to open it),
   then "tidy up my desktop" (a preview card; say "yes", then "undo").
6. Press the chord mid-answer: speech stops at once and it listens again.

If the hotkey does nothing, grant Accessibility (and Input Monitoring, if
macOS asks) to the app or terminal that runs it, then restart Plip.

## Privacy

- Audio is streamed only while the chord is held.
- Screenshots go only to the configured model, and only for questions that need
  them.
- Plip's own windows (island, mascot) are excluded from captures.
- Memory, history and the action log stay in `~/.config/mcp-vision` and the
  state folder. Sensitive facts are never put in a prompt.
- Subscription CLIs run in an empty temporary folder with tools off, and Plip
  deletes the folder (and its screenshots) after each answer.
- Keys live in `~/.config/mcp-vision/.env` with mode 0600, written by Settings or
  `plip setup`. History stays local and can be cleared in Settings.

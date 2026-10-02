# Blip

`blip` (also `mcp-vision buddy`) is a voice companion that lives in the MacBook
notch. You hold Control+Option and talk. The notch island shows what it heard and
what it is doing. It looks at your screens, answers out loud, and Blip, a small
mascot by your cursor, flies to whatever it is talking about.

The product model and most of the tuning come from a source-level reading of
[farzaa/clicky](https://github.com/farzaa/clicky): its prompt, POINT protocol,
1280 px JPEG captures, flight curve, bubble timing, hotkey, AssemblyAI and
ElevenLabs settings. The differences are deliberate and are listed in the README.

## Modules (`src/mcp_vision/buddy/`)

| Module | Role | Platform |
|---|---|---|
| `companion.py` | Runs one turn: route, capture and screen map in parallel, stream the brain, split it into speech and points, run walkthroughs, record history, handle barge-in. Emits observer events (phase, step, engine, answer, point, walkthrough, done, error) | any |
| `pointing.py` | Streaming parser for `[POINT:x,y:label:screenN]`, `[STEPS:n]` and `[DONE]`. Each point is attached to the sentence that mentions it. Malformed tags are dropped, never spoken | any |
| `prompt.py` | Blip's system prompt (ear-first answers, pointing, screen-map coordinates, guided walkthroughs) plus the text-only variant | any |
| `engines.py` | Brains on the user's own plan. Finds and probes Claude Code, Codex, Cursor CLI and Gemini CLI (installed? signed in?), picks the best one ready, and streams each through its JSON output with tools off in an empty scratch folder | any |
| `brain_claude.py` | Anthropic API brain: streaming, cached system prompt, `fallbacks="default"`, per-turn effort | any |
| `screen_context.py` / `ax_context.py` | The screen map: frontmost app, window, selection, and visible controls with their centers in screenshot pixels | any / macOS |
| `watch.py` | Screen fingerprints (64×40 grayscale) and a watcher that waits for the user's action to land and settle | any |
| `jev.py` / `router.py` | TypeSafe Jev client, and one parallel Jev request that answers: needs screen? intent? walkthrough? cursor screen only? Rules cover the same questions when Jev is unavailable | any |
| `snap.py` / `ax_locator.py` | Moves the model's estimate onto the accessibility element it names. Jev picks the element, with an explicit "none" | any / macOS |
| `capture.py` / `geometry.py` | Every display as a JPEG (longest side 1280 px, cursor screen first), plus pixel ↔ point ↔ AppKit mapping | any (mss) |
| `flight.py` / `animator.py` | Bezier flight, follow, point, hold, return. The state machine is driven by `tick(now, mouse)` | any |
| `speech_out.py` / `speech_in.py` | Sentence-pipelined TTS (ElevenLabs, `say`) and AssemblyAI v3 / Apple Speech input | any / macOS audio |
| `controller.py` | The press / release / transcript / barge-in state machine | any |
| `presenter.py` | Turns controller and companion events into island and mascot UI messages | any |
| `settings_service.py` / `store.py` | Backs the Settings window: snapshot, key saving (0600), engine choice, depth, voice, history, permissions | any |
| `web_host.py` | Hosts the React bundle (`web/index.html`) in a transparent WKWebView, with a JSON bridge each way | macOS |
| `island_macos.py` | The notch island: a click-through panel above the menu bar on every Space, sized from the real notch (`safeAreaInsets`, auxiliary areas) | macOS |
| `mascot_macos.py` | Blip by the cursor: native 60 Hz motion from the animator, with the character drawn by the web view | macOS |
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

`blip ask --json` prints per-stage timings:
`looked`, `first_token`, `first_speech`, `first_point`, `model_done`, and
`spoken`.

## Coordinates

The model answers in the pixels of the screenshot it saw. `Screenshot.to_global`
scales those pixels by the display's point size and offsets them by the display
origin, giving global top-left points. That is the space Accessibility uses, so
the snapper can work in it directly. The overlay converts to AppKit's
bottom-left space only when it moves its window.

## Brains on your own plan

| Engine | Command Blip runs (per question) | Streaming | Images |
|---|---|---|---|
| Claude Code | `claude -p --input-format stream-json --output-format stream-json --include-partial-messages --system-prompt … --tools "" --safe-mode --strict-mcp-config --no-session-persistence --effort low` | token deltas | base64 image blocks on stdin |
| Codex | `codex exec --json --sandbox read-only --ephemeral --skip-git-repo-check -c model_reasoning_effort="low" --image screen1.jpg -- -` | per message | `--image` files |
| Cursor | `agent -p --output-format stream-json --stream-partial-output --mode ask --trust --workspace <tmp> "<prompt>"` | token deltas | none: Blip sends the screen map instead |
| Gemini | `gemini --output-format stream-json --skip-trust --prompt "@screen1.jpg …"` with `GEMINI_SYSTEM_MD` set to Blip's prompt | token deltas | `@file` references |

Sign-in probes: `claude auth status --json`, `codex login status`,
`agent status --format json`, and `~/.gemini/oauth_creds.json`. If no answer starts
within 45 s (for example, a CLI waiting for the network), the turn ends with a spoken
explanation instead of silence.

## Manual check on a Mac

CI covers everything except real AppKit, audio, and permission prompts. After
changing a macOS module, do these checks.

1. Run `blip doctor`. One brain should say `<- Blip thinks with this`. Jev may say
   `--` if it is not configured.
2. Run `blip`.
   - Settings opens on the first run. The Brain tab shows your signed-in plan as Ready.
   - The Blip glyph appears in the menu bar, and Blip trails the cursor.
   - Hovering the notch peeks the island out.
3. Hold Control+Option and say "where is the apple menu".
   - The island drops down with a live waveform and your words.
   - When you let go, it shows the steps it is taking ("Looked at 1 screen", "Claude is thinking").
   - Blip then speaks, flies to the Apple menu, shows "apple menu" in its bubble, and flies back.
4. Ask "how do I turn on dark mode". The island shows "Step 1 of n". Do the step;
   Blip notices the screen change and gives the next one.
5. Ask a long question, then press the chord again mid-answer. Speech stops at
   once, Blip returns, and it listens again.
6. Switch the brain in Settings and ask again. The island's engine chip changes.

If the hotkey does nothing, grant Accessibility (and Input Monitoring, if
macOS asks) to the app or terminal that runs it, then restart Blip.

## Privacy

- Audio is streamed only while the chord is held.
- Screenshots go only to the configured model, and only for questions that need
  them.
- Blip's own windows (island, mascot) are excluded from captures.
- Subscription CLIs run in an empty temporary folder with tools off, and Blip
  deletes the folder (and its screenshots) after each answer.
- Keys live in `~/.config/mcp-vision/.env` with mode 0600, written by Settings or
  `blip setup`. History stays local and can be cleared in Settings.

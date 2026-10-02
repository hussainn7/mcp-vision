# The cursor buddy

`mcp-vision buddy` is a voice companion that lives next to the mouse pointer.
You hold Control+Option and talk. It looks at your screens, answers out loud,
and flies a blue triangle to whatever it is talking about.

The product model and most of the tuning come from a source-level reading of
[farzaa/clicky](https://github.com/farzaa/clicky): its prompt, POINT protocol,
1280 px JPEG captures, flight curve, bubble timing, hotkey, AssemblyAI and
ElevenLabs settings. The differences are deliberate and are listed in the README.

## Modules (`src/mcp_vision/buddy/`)

| Module | Role | Platform |
|---|---|---|
| `companion.py` | Runs one turn: route and capture in parallel, stream Claude, split it into speech and points, record history, handle barge-in | any |
| `pointing.py` | Streaming `[POINT:x,y:label:screenN]` parser. Each tag is attached to the sentence that mentions it. Malformed tags are dropped, never spoken | any |
| `prompt.py` | System prompt (Clicky's ear-first rules plus multi-step pointing) and screen labels | any |
| `brain_claude.py` | Anthropic SDK streaming with a cached system prompt, `fallbacks="default"`, and per-turn effort | any |
| `jev.py` | TypeSafe Jev client for `POST /v1/systemone` with choice / noul / score questions | any |
| `router.py` | One parallel Jev request answers: needs screen? intent? walkthrough? cursor screen only? Rules cover the same questions when Jev is unavailable | any |
| `snap.py` | Moves the model's estimate onto the accessibility element it names. Jev picks the element, with an explicit "none" | any |
| `capture.py` / `geometry.py` | Every display as a JPEG (longest side 1280 px, cursor screen first), plus pixel ↔ point ↔ AppKit mapping | any (mss) |
| `flight.py` / `animator.py` | Bezier flight, follow, point, hold, return. The state machine is driven by `tick(now, mouse)` | any |
| `speech_out.py` | Sentence-pipelined TTS (ElevenLabs, `say`, espeak, print) that stops instantly on interrupt | any |
| `speech_in.py` | AssemblyAI v3 streaming (Clicky's settings) or Apple Speech | macOS audio |
| `controller.py` | The press / release / transcript / barge-in state machine | any |
| `hotkey.py` | Control+Option chord detection, using a Quartz event tap or an NSEvent fallback | macOS |
| `overlay_macos.py` | A 60 Hz click-through window, excluded from screen capture | macOS |
| `ax_locator.py` | Hit-tests a ring of points around a target and climbs to the owning control | macOS |
| `app_macos.py` | Menu bar, permissions, and wiring | macOS |

Everything marked "any" is unit-tested on Linux CI (`tests/test_buddy_*.py`).
The macOS modules are thin. Every AppKit, Quartz, and AX symbol they use has been
checked against PyObjC's metadata.

## Where Jev helps

Jev is a "System-1" model. It returns typed answers (a choice with
probabilities, a yes/no probability, or a score) in about 100 ms. It cannot see
images or write text. The buddy uses it in two places where a full vision model
would be slow or overkill:

1. **Before Claude runs**, with one request and four parallel questions:
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
3. Claude streams. As soon as the first sentence is complete, its text-to-speech
   request starts.
4. Sentence N+1 is synthesized while sentence N plays.
5. A point tag starts the flight just before its sentence is spoken.

`mcp-vision buddy ask --json` prints per-stage timings:
`looked`, `first_token`, `first_speech`, `first_point`, `model_done`, and
`spoken`.

## Coordinates

The model answers in the pixels of the screenshot it saw. `Screenshot.to_global`
scales those pixels by the display's point size and offsets them by the display
origin, giving global top-left points. That is the space Accessibility uses, so
the snapper can work in it directly. The overlay converts to AppKit's
bottom-left space only when it moves its window.

## Manual check on a Mac

CI covers everything except real AppKit, audio, and permission prompts. After
changing a macOS module, do these five checks.

1. Run `mcp-vision buddy doctor`. Every line should say `ok`; Jev may say `--`
   if it is not configured. Run `--ping` once to verify the TypeSafe key.
2. Run `mcp-vision buddy`.
   - The menu-bar triangle appears.
   - The blue buddy trails the cursor at the lower right.
   - The status reads "Ready - hold Control+Option".
3. Hold Control+Option and say "where is the apple menu".
   - While you hold the keys, the waveform reacts to your voice.
   - When you release them, a spinner shows.
   - The buddy then speaks, flies to the Apple menu, types "apple menu" in a
     bubble, holds, and flies back.
4. Ask a long question, then press the chord again mid-answer. Speech stops at
   once, the pointer returns, and it listens again.
5. Ask "what's the capital of france". The answer is spoken with no pointing.
   With Jev configured, `--json` from `buddy ask` reports
   `needs_screen: false`.

If the hotkey does nothing, grant Accessibility (and Input Monitoring, if
macOS asks) to the app or terminal that runs it, then restart Buddy.

## Privacy

- Audio is streamed only while the chord is held.
- Screenshots go only to the configured model, and only for questions that need
  them.
- The overlay window is excluded from captures.
- Keys live in `~/.config/mcp-vision/.env` with mode 0600, written by
  `mcp-vision buddy setup`.

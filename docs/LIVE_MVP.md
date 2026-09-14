# Existing-session MVP

The product target is an MCP action layer for existing Chrome and macOS, usable
from a model-capable host. Task briefs and disposable demos alone do not meet it.

## Evidence so far

- `serve --browser live` exposes tab listing, exact tab selection, and new-tab
  navigation in an existing profile. It shares the grounded action implementation.
- A real Chromium integration test connects two independent drivers to one profile,
  reads its cookie, updates the same tab, blocks Send, and verifies that tabs and
  cookies survive runtime disconnect. This is a controlled test profile, not proof
  of a successful personal-account task.
- Unit checks cover missing setup, loopback endpoint restrictions, MCP tool
  registration, and disconnect ownership.
- macOS now uses a native, bounded Allow once / Deny dialog even with piped MCP
  stdin, plus a notification so the prompt is hard to miss. Deny is the default.
- `mcp-vision probe --live` against an operator-approved Chrome session passed
  connect, open_tab, send/buy barriers, eBay research, Google Flights to SFO,
  Gmail observe-with-compose-blocked, and GSU D2L/iCollege weekly course home.
  Personal contents stay out of Git (`outputs/real_tasks/` is local only).
- `mcp-vision install --host cursor|claude-desktop|antigravity` defaults to live
  Chrome. Mission Control and README match that path.

## Still required

- Operator must enable Remote debugging once (`mcp-vision connect --wait`).
- Live email / iCollege proof needs that connection; `mcp-vision probe --live`
  exercises them without writing personal contents into Git.
- `mcp-vision probe` covers eBay research, SFO flight search, and send/buy
  barriers in disposable Chromium. Measure outcomes in `outputs/real_tasks/`.
- Desktop SoM remains experimental; browser-first is the MVP path.

Chrome's settings page is blocked by this development session's computer-use
policy. The user must enable Remote debugging manually; do not work around that
block with profile edits, browser restarts, or alternate automation surfaces.

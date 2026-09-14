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
  stdin. A harmless live display test received an explicit Allow once response;
  no browser action followed. Cancellation and timeout paths are covered by tests.
  A second live test expired and returned denied. The complete suite passed
  132 tests after these changes.

## Still required

- User-approved connection to the actual logged-in Chrome instance.
- Real email lookup, eBay comparison, flight search with user dates, and GSU
  iCollege weekly work. Keep personal contents out of Git and test fixtures.
- Native macOS observation/action coverage and a visible, bounded approval flow
  that works when MCP stdio has no interactive terminal.
- End-to-end execution from a real host and a local-model client, with installation
  instructions that someone else can follow.
- Measure task outcomes against explicit checks. Report failures and blockers;
  do not advertise a success rate from scripted fixtures.

Chrome's settings page is blocked by this development session's computer-use
policy. The user must enable Remote debugging manually; do not work around that
block with profile edits, browser restarts, or alternate automation surfaces.

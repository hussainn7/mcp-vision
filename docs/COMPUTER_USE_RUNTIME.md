# Computer-use runtime status

This document describes what is implemented and what was actually exercised in
the September 21, 2026 development pass.

## Runtime path

System-2 can delegate one routine subgoal to FastPath. The local rules policy or
optional Jev adapter chooses only from candidates compiled for the current
immutable state. Every attempted candidate is consumed before dispatch, the
successor is observed, and a separate verifier decides whether the subgoal is
complete. Low confidence, ambiguity, restricted actions, repeated actions,
no-ops, stale-state exhaustion, and step-budget exhaustion all stop or escalate.

Browser actions prefer DOM operations and run in the background in isolated
Chromium. The native Chrome driver is honest about foreground activation. On
macOS native apps, the runtime tries background AXPress/AXValue, then PID-targeted
keyboard delivery, then foreground AX/keyboard/pointer fallbacks. Receipts show
the chosen path and every failed attempt.

Unified perception can merge DOM, AX, OCR, and visual identities into one
element. It is semantic-first: visual grounding is not invoked when reliable
actionable semantic controls already exist. The visual provider is injectable;
no heavyweight visual model is bundled or silently contacted. Visual-only
pointer fallback is guarded by screenshot freshness and normal safety policy.

## Operator experience

The Studio live demo now runs the actual rules FastPath. Its session view shows
the goal, current subgoal, status, policy confidence, execution path, focus
behavior, verification, guardrails, element highlight, and a clickable timeline.
Before/after browser images and exported evidence are available without exposing
chain-of-thought.

The standalone trace viewer accepts both legacy JSONL traces and transaction
replay JSON. Replay cards include before/after states, the selected target,
other candidates, target metadata, policy summary, confidence, execution path,
diff, timing, receipt, and verification.

## Reproducible checks

```bash
MCP_VISION_BROWSER_TESTS=1 python -m pytest
mcp-vision bench-fastpath --iterations 10
mcp-vision probe --isolated --headless --no-summarize
python trace_viewer.py replay.json
```

The final full suite in this pass completed 369 tests before documentation-only
edits. The public disposable-browser probe completed 7/7 checks after a stale
eBay URL was repaired; account-dependent live-profile checks are explicitly
reported as skipped in isolated mode. See [BENCHMARKS.md](BENCHMARKS.md) for the
30-run-per-strategy comparison and its limitations.

## Honest limitations from this machine

- `TYPESAFE_API_KEY` was not present. The Jev protocol, simultaneous operation/
  target heads, validation, and fallback tests are ready, but no live Jev result
  is claimed.
- macOS Accessibility and Screen Recording permissions were not enabled for the
  development process, so native behavior was covered with deterministic AX and
  PID-input tests but could not be dogfooded in TextEdit on this host.
- The visual fallback has a provider interface, fusion, caching, and guarded
  execution. A production GUI-Actor/OmniParser service is not bundled.
- Learned workflows are returned as candidates and can be stored in the local
  workflow library. Automatic unattended replay is intentionally not enabled.
- The local benchmark excludes model latency and cost. It demonstrates fewer
  planner handoffs, not a fabricated System-2 speedup.

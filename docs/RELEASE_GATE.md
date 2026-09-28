# MVP release gate

The gate is fail-closed: automated checks and installed-product dogfood are
recorded separately, and all six live categories must pass on the recorded Git
SHA before the result can be `go`. Scripted, mocked, or isolated-browser results
never satisfy a live category.

## Checklist

1. Build and install `/Applications/MCP-Vision.app` from the report's Git SHA.
2. Initialize a private artifact directory:
   `.venv/bin/python scripts/release_gate.py --report outputs/release_gate/report.json init`
3. Run the automated matrix with `run-automated`. It covers the full suite,
   browser integrations, installed-wheel smoke, replay schema, hardcode audit,
   and release-report validation.
4. Through the installed popup, exercise every named live category. Use the
   physical hotkey and hold-to-talk for at least one native and one browser run.
   Confirm final app/browser state independently and verify mouse/focus behavior.
5. Copy only scrubbed screenshots and replay bundles into the report directory.
   Do not include account names, messages, page content, transcripts, prompts,
   clipboard data, or credentials. Use `record-live` for each category. Replay
   counters are derived automatically; explicit `--count` and `--latency`
   values may add metrics not present in an older replay.
6. Run `validate --complete`. Any missing, blocked, failed, unhashed, or
   semantically unverified category is a release-blocking `no-go`.

Example evidence recording (paths must be inside the report directory):

```sh
.venv/bin/python scripts/release_gate.py \
  --report outputs/release_gate/report.json record-live calculator_arithmetic \
  --status pass --task-id task-123 --input-mode hotkey_voice \
  --delivery --semantic --execution-path native_ax \
  --screenshot outputs/release_gate/calculator.png \
  --replay outputs/release_gate/calculator.replay.json
```

Failed or blocked runs use a short reproducible blocker code, for example
`--status blocked --blocker microphone_permission_denied`. The command and
artifact hashes provide reproduction identity without embedding raw content.

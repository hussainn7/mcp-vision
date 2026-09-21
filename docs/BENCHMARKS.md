# FastPath benchmark

`mcp-vision bench-fastpath` runs three deterministic workflows in real disposable
Chromium: a two-step draft form, an autocomplete control that rerenders and uses
SPA navigation, and a modal settings control. Each final state has a
machine-checkable completion predicate.

The comparison holds the browser runtime, semantic actions, task inputs, and
verification constant:

- `foundation_stepwise` models the foundation runtime handoff pattern: observe,
  let the planner choose one semantic action, execute it, then return to the
  planner for the next action.
- `rules_fastpath` delegates the whole subgoal once, then uses the bounded local
  rules policy until the predicate verifies or the loop escalates.

## Measured result

Measured on 2026-09-21 on the development Mac, with 10 iterations per task (30
complete runs per strategy):

| Metric | Foundation stepwise | Rules FastPath |
|---|---:|---:|
| Task success | 30/30 | 30/30 |
| Median runtime-only task time | 150.75 ms | 152.96 ms |
| Planner handoffs | 60 | 30 |
| Actions | 60 | 60 |
| Observations | 90 | 90 |
| Wrong-target actions | 0 | 0 |
| Verification failures | 0 | 0 |
| Background actions | 60 | 60 |
| Foreground actions | 0 | 0 |

The local FastPath is not intrinsically faster when model latency is excluded;
it was 2.21 ms slower at the median in this run. Its demonstrated benefit is
cutting planner handoffs in half while preserving identical semantic work and
verified success. No System-2 latency or cost is estimated, so this report does
not claim an invented wall-clock speedup.

Run it again on the current machine:

```bash
mcp-vision bench-fastpath --iterations 10 --output outputs/fastpath-benchmark.json
```

The JSON includes every individual run and the aggregate. `system2_calls` is
explicitly `null`: this local benchmark does not call a model. Jev is also not
included unless `TYPESAFE_API_KEY` is configured and a separate provider run is
performed.

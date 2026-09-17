# The reasoning harness

`src/mcp_vision/reasoning/` is a **general decision-making layer**, independent of
the browser/desktop runtimes. It is not a task-specific workflow system and it
owns no real execution — it decides; the runtimes own reality.

## The core idea

- **The model owns judgment.** The reasoning layer decides what the user wants,
  what matters, what to infer, what assumptions are fair, what to investigate,
  what the best next action is, and when the *actual goal* is satisfied.
- **The runtime owns reality.** Observations, permissions, identity, verification
  and consequence enforcement stay in the runtimes and their governors.

So a request like `find flights to sf` does **not** fail because dates were
omitted: the harness makes reversible assumptions (dates flexible, one traveler,
economy) for research, and only demands certainty before anything consequential
like purchasing.

## Modules

| Module | Responsibility |
|---|---|
| `schemas.py` | Strong types: `AgentState`, `Assumption`, `Uncertainty`, `Goal` vs `TaskItem`, `Strategy`, `ProposedAction` with `ExpectedOutcome`, `Verification` (action/state/goal), `Progress`, `Completion`, `MemoryBank`, `Decision`. |
| `intent.py` | Raw request → a compact, structured understanding (objective, explicit/inferred constraints, preferences, consequence level). Fast-path extraction only. |
| `uncertainty.py` | Closes unknowns context → observe → discover → infer → reversible assumption → only then ask. |
| `consequence.py` | Consequence levels 0–4, shadow consequences, and `autonomy_allowed` ramp-down. |
| `verify.py` | Three levels of verification kept distinct (did it run / did the state change / did the goal advance). |
| `strategies.py` | Failure classification → recovery advice; strategy-switch signal on stall. |
| `budget.py` | Progress tracking and a smart resource budget, not a hard "N steps = impossible" cap. |
| `memory.py` | Working vs session vs durable-preference vs capability separation. |
| `model_routing.py` | Route work to fast / general / strong reasoning tiers by difficulty and stakes. |
| `reasoners.py` | `ModelReasoner` (token-efficient chat → `Decision`) and a `HeuristicReasoner` fallback/smoke path. |
| `harness.py` | The persistent loop: understand → state → uncertainty → strategy → next action → consequence check → execute → observe → verify → update → evaluate goal → continue/replan/finish. |

## Wiring it in

`reasoning/runtime.py` adapts an existing runtime backend (the same interface
`ContextTask` uses) into the harness `Executor` protocol, reusing
`resolve_target` and `TaskConstraints` — the smallest clean insertion point that
preserves the runtime's safety model. The harness itself is runtime-agnostic and
is tested standalone with fake executors.

Evals live in `tests/test_reasoning_evals.py`: vague, heterogeneous prompts
(`find me something decent`, `clean this up`, `figure out why checkout is
broken`, `help me apply here`, …) measured on objective quality, reversible
assumptions, unnecessary-clarification rate, forward progress, consequence
gating, and correct stopping — deliberately too broad for keyword hardcoding.

```bash
pytest tests/test_reasoning_policies.py tests/test_reasoning_harness.py tests/test_reasoning_evals.py
```
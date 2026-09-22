# State-scoped runtime

MCP-Vision's bounded path makes the runtime, rather than a model, authoritative
about current UI reality:

```text
browser_observe -> immutable state + @e refs + candidates
browser_choose_candidate -> optional bounded policy choice
browser_execute_candidate -> freshness/safety gate -> input -> successor -> diff -> postcondition
browser_fastpath -> bounded observe/choose/execute/verify loop -> done/escalate
```

Every state has a random `state_id`, a root identity, and a monotonically
increasing root epoch. Element refs and candidate IDs are valid only in that
state. A newer observation of the same root makes the older epoch ineligible
for transaction execution. A candidate is consumed before dispatch so an
ambiguous transport failure can never cause a blind replay.

`browser_execute_candidate` returns the primitive backend receipt separately
from the semantic transaction status. A successful field read-back proves only
that primitive; the transaction is `verified` only when its optional semantic
postcondition is observed in the successor state. `task_complete` remains
false: task completion belongs to an independent goal checker.

The default `rules` fast policy is deliberately conservative. Configure the
server with `--fast-policy jev` to enable the optional TypeSafe/Jev adapter and
set `TYPESAFE_API_KEY` (optionally `TYPESAFE_MODEL` and `TYPESAFE_ENDPOINT`).
Without credentials, it requests System-2 replanning and MCP-Vision continues
to work. Available policies are `jev`, `rules`, `local`, `system2`, and
`disabled`. Policies choose only supplied IDs and never execute actions.

FastPath accepts a structured subgoal, named input values, and a reusable
verification predicate. It has independent step, retry, confidence, no-op, and
repeated-action limits. Restricted candidates are never passed to the fast
policy. `subgoal_complete` becomes true only after the predicate passes;
`task_complete` remains false because the delegating planner still owns the
larger task.

The execution receipt records the substrate actually used (`dom`,
`native_browser_dom`, `ax_background`, `pid_keyboard`, foreground keyboard or
pointer, or guarded `visual_pointer`) and whether it kept the user's focus.
Native AX actions are tried without activation first. A fallback occurs only
after a checked failure; an unknown delivery is never blindly replayed.

DOM or accessibility semantics remain preferred. OCR/visual grounding should
only add candidates when semantic sources cannot represent the control; any
visual region must remain bound to the immutable capture that produced it.
`mcp_vision.perception` merges corroborating identities and sources, skips a
visual provider when semantic controls suffice, and can reuse unchanged pixel
tiles. Visual-only clicks require a fresh identical capture and remain outside
routine FastPath execution.

`browser_replay_bundle` returns the bounded states and structured events needed
to inspect selected and alternative candidates, policy confidence, execution
path, before/after state, diff, receipt, and verification. It contains concise
decision summaries, not private chain-of-thought. `browser_session_memory`
compresses older structured history while retaining recent evidence.

Verified replays can be distilled with `browser_workflow_candidate`. Learned
steps use site/application anchors, semantic roles and names, optional DOM/AX/
visual identities, safety classifications, and postconditions—never pixel
coordinates. Changed identities may be repaired semantically; ambiguity, a
wrong site, or elevated risk replans or blocks.

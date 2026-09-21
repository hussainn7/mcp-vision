# State-scoped runtime

MCP-Vision's bounded path makes the runtime, rather than a model, authoritative
about current UI reality:

```text
browser_observe -> immutable state + @e refs + candidates
browser_choose_candidate -> optional bounded policy choice
browser_execute_candidate -> freshness/safety gate -> input -> successor -> diff -> postcondition
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

DOM or accessibility semantics remain preferred. OCR/visual grounding should
only add candidates when semantic sources cannot represent the control; any
visual region must remain bound to the immutable capture that produced it.

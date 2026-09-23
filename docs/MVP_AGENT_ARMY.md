# MCP-Vision MVP technical direction and agent army

## Principal-engineering verdict

MCP-Vision has good pieces but not one product architecture. The shipped popup runs
`ContextTask` in `src/mcp_vision/tasks.py`, with a second `Step` schema, a second
boolean verifier, regex routing, and direct backend dispatch. The newer foundation in
`state.py`, `transactions.py`, `verification.py`, and `fastpath.py` has immutable
observations, state-scoped candidates, one-shot mutation discipline, diffs, explicit
postconditions, and bounded policy selection, but the popup largely bypasses it.

That split explains the misleading test surface: isolated state/runtime tests can pass
while the real hotkey path fails halfway through a native task. A live Calculator run
on 2026-09-23 opened from the actual global hotkey/popup, observed the real keypad,
advanced `7 -> 1 -> 12 -> 12+`, then stopped and surfaced an input/failure result
instead of producing `19`.

The other fundamental problems are:

- Native perception is not yet authoritative about the exact target window, capture
  scale, completeness, or same-PID sibling ambiguity.
- Browser perception is a useful Playwright snapshot but is weaker than CDP
  DOMSnapshot + full AX-tree fusion for iframes, shadow DOM, paint order, and stable
  backend node identity.
- Clarification survives in popup fields/history, not a durable typed task checkpoint
  with missing slots, plan cursor, completed effects, and epoch revalidation.
- `plan.py`, `controller.py`, `request_routing.py`, `partial_intent.py`, and the planner
  prompt contain flight-specific behavior. `tasks.py` contains an arithmetic keypad
  compiler and Calculator instructions; deterministic arithmetic is legitimate, but
  it belongs behind a general semantic `ENTER_EXPRESSION` capability, not inside the
  main planner loop.
- `native_apps.py` promotes “new note” to a parser-level command. App discovery and
  standard commands are valid capabilities; note-specific intent is not a scalable
  task ontology.
- Provider support exists, but routing is mostly provider-name selection. It does not
  benchmark or enforce separate fast-policy, writer, and strong-review contracts.
- Partial speech only warms a model and contains a flight branch. It does not capture
  the screen, connect the browser, or prepare a reversible task generically.

## What to preserve

Preserve `UIState`, state epochs, one-shot candidate consumption, `ExecutionTrace`,
the governor, DOM/AX-first execution, the provider-neutral backend boundary, Studio
replay, and the existing safety rule that input delivery is not task completion.

Two foundational commits now strengthen this spine:

- `0793630 verify states`: immutable observation quality/provenance and
  `satisfied | unsatisfied | unknown` verification, with compatibility output.
- `3f45a8f degraded guards`: FastPath refuses to act when degraded perception makes
  completion unknowable.

## Source-research conclusions

- CUA's best ideas are exact window/capture authority, snapshot-bound tokens,
  background-input refusal rules, and stable-sample tri-state verification. Adapt the
  contracts, not its large cross-platform stack or separate visual-region stream.
  See [`get_window_state.rs`](https://github.com/trycua/cua/blob/912a4550b72a770f3a21e1f3453961a9eca9de08/libs/cua-driver/rust/crates/platform-macos/src/tools/get_window_state.rs),
  [`background_input.rs`](https://github.com/trycua/cua/blob/912a4550b72a770f3a21e1f3453961a9eca9de08/libs/cua-driver/rust/crates/cua-driver-core/src/background_input.rs), and
  [`verification.rs`](https://github.com/trycua/cua/blob/912a4550b72a770f3a21e1f3453961a9eca9de08/libs/cua-driver/rust/crates/cua-driver-contract/src/verification.rs).
- Pi Computer Use has the closest foundation: state/resource epochs, cached progressive
  disclosure, ScreenCaptureKit + AX + conditional OCR, background-first execution,
  successor observations, and confidence-aware diffs. Do not copy its per-operation
  CDP reconnects, direct DOM value mutation, or optional verification. See
  [`runtime.ts`](https://github.com/injaneity/pi-computer-use/blob/4b8dbd7eaa13328ab1a8a4b55d0be0b077de7d62/src/runtime.ts),
  [`bridge.ts`](https://github.com/injaneity/pi-computer-use/blob/4b8dbd7eaa13328ab1a8a4b55d0be0b077de7d62/src/bridge.ts), and
  [`actions.ts`](https://github.com/injaneity/pi-computer-use/blob/4b8dbd7eaa13328ab1a8a4b55d0be0b077de7d62/src/actions.ts).
- Browser Use is strongest at concurrent CDP DOMSnapshot/full-AX collection, iframe and
  shadow handling, paint-order/occlusion data, adaptive screenshot inclusion, and
  multi-action invalidation. Do not copy naked selector indices, fixed sleeps, or
  task-specific watchdog actions. See [`dom/service.py`](https://github.com/browser-use/browser-use/blob/d8110c5ff87ccba887aaa726cdb780f2f84bef8d/browser_use/dom/service.py) and
  [`dom_watchdog.py`](https://github.com/browser-use/browser-use/blob/d8110c5ff87ccba887aaa726cdb780f2f84bef8d/browser_use/browser/watchdogs/dom_watchdog.py).
- Jev Ultrafast proves that one request can select an operation and speculative
  operation-specific targets from finite options, while code validates only the chosen
  head. Jev must not write text, invent actions, perceive screenshots, or verify goals.
  Its own flight benchmark is useful but narrow. See
  [`model.py`](https://github.com/browser-use/jev-ultrafast/blob/1231850a0bf1a0c0341fe408ef1668dbbfdfac46/jev_ultrafast/model.py) and
  [`performance.md`](https://github.com/browser-use/jev-ultrafast/blob/1231850a0bf1a0c0341fe408ef1668dbbfdfac46/docs/performance.md).
- TypeSafe's best pattern is fast finite-choice policy until ambiguity/stall, then a
  stronger writer/reviewer that returns a narrow focus or question—not coordinates.
  Its OCR tile cache is worth benchmarking. Do not copy its mixed “did typing land and
  was it sensible?” verifier. See
  [`runner.py`](https://github.com/awlevin/typesafe-computer-use/blob/c96dbddd04ea151c3b21cbd79c6b59c0888cc473/typesafe_computer_use/runner.py) and
  [`perception.py`](https://github.com/awlevin/typesafe-computer-use/blob/c96dbddd04ea151c3b21cbd79c6b59c0888cc473/typesafe_computer_use/perception.py).
- OpenAI's sample supports a persistent, bounded local browser-program fast path and
  provider continuation IDs; Anthropic's quickstart supports image budgets, stable
  cache prefixes, compaction, interruption records, and stop-on-first-error batches.
  Do not use arbitrary generated code or provider transcripts as MCP-Vision's domain
  model.

## Chosen architecture

```text
InputSession (typed / final speech / stable partial)
  -> TaskState (goal, slots, plan cursor, effects, pending question, continuation)
  -> Planner (System-2 only for decomposition/ambiguity)
  -> SemanticAction (operation, target, args, preconditions, postconditions, focus policy)
  -> State-bound candidate compiler
  -> Optional fast policy (rules first, Jev only when benchmarked)
  -> Execution router (DOM/CDP or AX first; foreground/visual last)
  -> condition-based wait
  -> immutable successor Observation (DOM/AX/OCR/vision fused with quality)
  -> tri-state stable verification
  -> continue / ask / reobserve / replan / finish
```

The popup, MCP tools, Studio, and replay must call this one runtime. There must not be
an “MVP loop” and a separate “foundation loop.”

## Ordered shortest credible MVP plan

1. Land exact native observation and quality reporting already started in the dirty tree.
2. Add stable verification and condition-based waits.
3. Make native delivery exact-window and background-first with honest unknown outcomes.
4. Route the real popup through the state/transaction/FastPath spine with durable task state.
5. Upgrade the browser observation/execution fast path and persistent connection.
6. Add explicit provider roles and benchmark Jev instead of assuming it helps.
7. Make clarification/resume and speech-time preparation generic.
8. Delete flight/note/Calculator planner special cases after equivalent general behavior exists.
9. Run a real UI release gate across the six MVP categories.

---

## Worker 1 — finish exact native multimodal observation

### GOAL
Finish and commit the current uncommitted AX + exact-window screenshot + Apple Vision
OCR fusion work as one trustworthy native observation path.

### WHY IT MATTERS
Native absence and coordinates are unsafe unless pixels and AX come from the exact same
window and carry completeness/provenance.

### CURRENT STATE
`pyproject.toml`, `src/mcp_vision/native_context.py`, the untracked
`src/mcp_vision/native_perception.py`, and `tests/test_native_perception.py` contain
user-owned work. The OCR test has been intermittent. `ObservationQuality` now consumes
`identity.fallback_reasons`. `scripts/build_macos_app.py` is an unrelated dirty icon
edit: do not stage it.

### EXACT FILES / FUNCTIONS TO INSPECT
`native_perception.py::{capture_window,recognize_text,degradation_reasons,fuse_ax_ocr}`;
`native_context.py::{nearby_ax,NativeContextBackend.snapshot,screenshot}`;
`state.py::{ObservationQuality,compile_state}`; `tests/test_native_perception.py`;
`tests/test_native_snapshot.py`; `tests/test_perception.py`.

### WHAT TO IMPLEMENT
Bind capture to a proven WindowServer window ID and PID; validate capture bounds and
scale; run AX and capture concurrently; invoke OCR only for explicit degradation;
merge OCR into compatible AX controls with provenance/confidence; create OCR-only
elements only when uniquely actionable; report structured unresolved degradation;
cache the exact capture by observation ID; make synthetic OCR testing deterministic.

### WHAT NOT TO TOUCH
No planner/routing/provider changes. Do not change the icon build edit. Do not add a
cloud vision dependency. Do not claim the whole tree complete merely because OCR ran.

### NO-HARDCODING REQUIREMENTS
No Calculator labels, app bundle IDs, fixed coordinates, or task phrases. Fusion must
use geometry, role compatibility, provenance, and confidence.

### REAL UI TEST TO PERFORM
Through `/Applications/MCP-Vision.app`, invoke on Calculator and one editor. Record the
exact window ID/bounds, show a blank AX-labelled Calculator digit gains its OCR label if
needed, and confirm the editor remains AX-only when complete. Verify no focus theft.

### AUTOMATED TESTS
Run native perception/snapshot/state tests. Add tests for wrong same-PID window,
Retina scale, OCR conflict, OCR-only uniqueness, capture failure, and quality reasons.

### SUCCESS CONDITION
One observation has one target-window authority, fused elements have correct sources,
and missing evidence remains degraded/unknown.

### COMMIT INSTRUCTIONS
Stage only the perception dependency/module/integration/tests. Commit `perception seam`.

### SHORT FINAL REPORT FORMAT
`Commit / exact-window proof / fusion cases / tests / real UI evidence / remaining gap`.

---

## Worker 2 — stable verification and condition waits

### GOAL
Extend tri-state verification into stable, repeated observations and replace fixed
settle sleeps with bounded predicate waits.

### WHY IT MATTERS
A transient match is not completion; a degraded miss is not failure.

### CURRENT STATE
`verification.py` exposes `satisfied|unsatisfied|unknown`; `fastpath.py` blocks action
when degraded perception makes completion unknown. `transactions.py` still has a
separate boolean postcondition implementation. `ContextTask.verify` is a third verifier.

### EXACT FILES / FUNCTIONS TO INSPECT
`verification.py`; `transactions.py::{Postcondition,PostconditionResult,_verify}`;
`fastpath.py::FastPath.run`; `tasks.py::ContextTask.verify`; backend `settle` and
`wait_for` methods; verification/state/runtime tests.

### WHAT TO IMPLEMENT
One predicate schema/result for all runtimes; stability policy with 1–5 consecutive
samples, 100 ms default cadence, 10 s hard cap, and final-match-without-stability =
unknown; observation-only checks that do not rotate action authority; operation-aware
readiness predicates; explicit preexisting outcome where useful.

### WHAT NOT TO TOUCH
Do not redesign planning or provider routing. Do not weaken confirmation policy. Do not
turn generic state change into semantic success.

### NO-HARDCODING REQUIREMENTS
No website/app text, task names, or fixed post-action sleeps. Wait on semantic
conditions selected from the operation and explicit postcondition.

### REAL UI TEST TO PERFORM
Use the real popup on Calculator and TextEdit. Demonstrate that a delayed display/value
settles to satisfied, an impossible predicate becomes unsatisfied, and incomplete
perception returns unknown without replaying the mutation.

### AUTOMATED TESTS
Clock-controlled tests for stable samples, last-moment match, timeout, degraded
negative evidence, preexisting state, and one-shot mutation discipline.

### SUCCESS CONDITION
Every meaningful action returns delivery and semantic verification separately; no
caller promotes `unknown` to success.

### COMMIT INSTRUCTIONS
Commit `verify states` or, if that subject exists, `stable verify`.

### SHORT FINAL REPORT FORMAT
`Commit / unified contract / wait policies / tests / real UI evidence / callers left`.

---

## Worker 3 — exact native execution router

### GOAL
Make native actions choose the fastest reliable delivery method without stealing focus
unless escalation is necessary and authorized.

### WHY IT MATTERS
Current PID keyboard delivery can be ambiguous across same-PID sibling windows, and a
timeout after input must not trigger a duplicate action.

### CURRENT STATE
`native_context.py` implements AX, PID keyboard, foreground keyboard/pointer fallbacks;
`execution_ladder.py` has `worked|didnt|unknown` and prevents fallback after unknown.

### EXACT FILES / FUNCTIONS TO INSPECT
`native_context.py::{click,fill,select,set_checked,act}`; `execution_ladder.py`;
`macos_input.py`; `native_apps.py`; native execution/stealth/input tests.

### WHAT TO IMPLEMENT
Fresh exact-window preflight; typed refusal reasons for hidden/minimized/sibling
ambiguity; semantic AX press/value first; targeted keyboard only with one proven
destination; foreground keyboard/pointer only as policy-approved escalation; bind
visual pointer to the exact fresh capture; emit every attempt and delivery outcome.

### WHAT NOT TO TOUCH
No app-specific AppleScript workflows, planner regexes, or new model calls. Do not retry
click/pointer after unknown delivery.

### NO-HARDCODING REQUIREMENTS
No shortcut tables keyed by app or task. General keyboard commands may be represented
semantically and resolved against advertised menu/key equivalents.

### REAL UI TEST TO PERFORM
Operate Calculator and TextEdit while another app remains foreground where possible;
verify background AX/value routes, then force one controlled foreground escalation and
prove focus behavior in the receipt.

### AUTOMATED TESTS
Same-PID ambiguity, hidden/minimized window, failed AX then safe fallback, unknown
delivery no replay, stale capture, and complete attempt trace.

### SUCCESS CONDITION
Every native receipt truthfully identifies target authority, method, focus behavior,
delivery outcome, and successor observation.

### COMMIT INSTRUCTIONS
Commit `executor stuff`.

### SHORT FINAL REPORT FORMAT
`Commit / route matrix / refusal cases / tests / real UI evidence / known limitation`.

---

## Worker 4 — put the real popup on the semantic runtime spine

### GOAL
Route shipped contextual tasks through `UIState -> ActionCandidate -> TransactionRuntime
-> VerificationEngine` and introduce a durable provider-neutral `TaskState`.

### WHY IT MATTERS
This removes the duplicate architecture that caused the real Calculator failure while
foundation tests passed.

### CURRENT STATE
`ContextTask` owns the popup path and directly dispatches `Step`. `FastPath` and
`TransactionRuntime` are used by MCP/Studio paths. Popup continuation is stored across
`macos_ui.py` fields/history rather than a typed checkpoint.

### EXACT FILES / FUNCTIONS TO INSPECT
`tasks.py::{Step,ContextTask.run,dispatch,verify}`; `macos_ui.py` submission/pending
request flow; `state.py`; `transactions.py`; `fastpath.py`; `session_memory.py`;
`execution.py::bind_context_backend`; context/controller/session tests.

### WHAT TO IMPLEMENT
A `TaskState` model with goal, status, supplied/missing slots, pending question,
subgoal/plan cursor, completed verified effects, observation IDs, cancellation token,
provider continuation, and metrics. Add a thin adapter so the popup executes semantic
candidates through transactions. Checkpoint before `ASK_USER`; merge the reply into the
same task; revalidate epochs; resume at the cursor. Retire direct boolean verification.

### WHAT NOT TO TOUCH
Do not rewrite UI layout, browser internals, or provider SDKs. Do not remove legacy code
until the popup path has parity and tests.

### NO-HARDCODING REQUIREMENTS
No task-specific slot names in core. Slots come from planner output/schema. No special
resume behavior for flights, forms, notes, or Calculator.

### REAL UI TEST TO PERFORM
Hotkey on Calculator for `12 + 7` must reach `19`; create a TextEdit document and type
unique text; run a deliberately ambiguous native request, answer the popup question,
and prove the same task ID resumes without repeating completed effects.

### AUTOMATED TESTS
Task serialization/resume, stale epoch on resume, cancellation, no duplicate mutation,
popup adapter parity, and an end-to-end fake backend using real transaction objects.

### SUCCESS CONDITION
The actual popup emits transaction/replay events and has no independent success logic.

### COMMIT INSTRUCTIONS
Use two commits if needed: `task state`, then `runtime spine`; keep each green.

### SHORT FINAL REPORT FORMAT
`Commits / removed bypasses / continuation proof / tests / real UI evidence / legacy left`.

---

## Worker 5 — persistent browser semantic FastPath

### GOAL
Upgrade browser observation to persistent CDP DOMSnapshot + full AX fusion and keep
browser protocol work bounded and reusable across steps.

### WHY IT MATTERS
Flight-style pages stress iframes, shadow roots, dynamic rerenders, autocomplete,
occlusion, and protocol latency.

### CURRENT STATE
`browser.py` uses Playwright handles; `live_browser.py` and `native_browser.py` attach to
existing Chrome; `phase2_mcp/page_snapshot.py` injects snapshot JS. State scoping exists,
but element identity/coverage are weaker than CDP backend-node identity.

### EXACT FILES / FUNCTIONS TO INSPECT
`browser.py::{snapshot,_target}`; `live_browser.py`; `native_browser.py`;
`phase2_mcp/page_snapshot.py`; `phase2_mcp/chrome_native.py`; `state.py::_identity`;
browser integration/live-attach tests and benchmark counters.

### WHAT TO IMPLEMENT
Persistent connection/session; concurrently capture DOMSnapshot, full AX trees for all
frames, viewport/DPR, and screenshot only when needed; fuse by backendNodeId; include
shadow/iframe offsets, paint order, visibility, occlusion, completeness, and pruning;
preflight target immediately before action; stop a local action batch when URL, root,
focus, or relevant guard changes; reuse successor observation.

### WHAT NOT TO TOUCH
No search-engine/site workflows, arbitrary model JavaScript, direct framework value
mutation, or Chrome-closing cleanup.

### NO-HARDCODING REQUIREMENTS
No fixed selectors, domain exceptions, pagination rules, or print/download task hacks.

### REAL UI TEST TO PERFORM
Against the user's real Chrome, navigate one public dynamic site and a local fixture;
exercise autocomplete, iframe/shadow element, rerendered stale target, background tab,
and confirm Chrome remains open and usable.

### AUTOMATED TESTS
Backend-node identity, iframe/shadow transforms, occlusion, detached frame tolerance,
stale preflight, protocol-call budget, screenshot degradation, and connection reuse.

### SUCCESS CONDITION
One browser step normally needs one fused observation, one action, and one successor;
no naked selector/index crosses a state boundary.

### COMMIT INSTRUCTIONS
Commit `browser fastpath`.

### SHORT FINAL REPORT FORMAT
`Commit / observation schema / protocol-call before-after / tests / real UI evidence / gaps`.

---

## Worker 6 — benchmarked intelligence roles

### GOAL
Make “bring your own intelligence” explicit and benchmark whether Jev helps each bounded
decision rather than enabling it by reputation.

### WHY IT MATTERS
Provider choice, policy choice, writer choice, and strong reasoning are different jobs.

### CURRENT STATE
`providers.py` maps friendly names; `backends.py` normalizes providers;
`reasoning/model_routing.py` names fast/general/strong tiers; `fast_policy.py` has rules,
mock, and Jev with speculative heads and confidence validation.

### EXACT FILES / FUNCTIONS TO INSPECT
`providers.py`; `backends.py`; `reasoning/model_routing.py`; `fast_policy.py`;
`readiness.py`; `config.py`; provider/reasoning/fastpath/benchmark tests.

### WHAT TO IMPLEMENT
Provider-neutral role config for `fast_policy`, `writer`, `planner`, `reviewer`, and
optional `vision`; capabilities and structured-output validation; continuation IDs;
fallback policy; benchmark runner comparing rules, Jev target-only, Jev operation+
target, cheap model, and strong model on frozen candidate sets with accuracy, latency,
cost, entropy/margin, invalid outputs, and escalations.

### WHAT NOT TO TOUCH
No cookie/session theft, billing bypass, local-model priority work, or secret logging.
Do not make Jev required.

### NO-HARDCODING REQUIREMENTS
Benchmark cases must cover multiple apps/sites and action types. No tuning only for the
flight fixture.

### REAL UI TEST TO PERFORM
Run at least one configured provider through the real popup and one Jev-enabled bounded
selection if credentials exist. If unavailable, report `configured_unverified`; never
claim a live result.

### AUTOMATED TESTS
Role resolution, unsupported capability, malformed structured output, timeout/fallback,
continuation round-trip, secret redaction, and deterministic benchmark report.

### SUCCESS CONDITION
An evidence table decides where Jev/rules/cheap/strong models run; configuration remains
provider neutral.

### COMMIT INSTRUCTIONS
Commit `model roles` and `policy bench` separately.

### SHORT FINAL REPORT FORMAT
`Commits / role matrix / benchmark table / tests / live evidence / recommendation`.

---

## Worker 7 — generic hold-to-talk preparation and continuation

### GOAL
Make speech feel fast by overlapping only safe reversible preparation, with typed input
and clarification using the same task lifecycle.

### WHY IT MATTERS
Current partial intent has a flight branch and only warms the provider.

### CURRENT STATE
`speech.py`, `partial_intent.py`, and `macos_ui.py::prepare_partial_intent` stream
partials; final text alone starts actions; metrics already record milestones.

### EXACT FILES / FUNCTIONS TO INSPECT
`speech.py`; `partial_intent.py::{preparation_for,PartialIntentWatcher}`;
`macos_ui.py::{start_voice,prepare_partial_intent,speech_partial,speech_final}`;
`interaction_metrics.py`; hotkey/speech/partial-intent/session tests.

### WHAT TO IMPLEMENT
Generic stable partial-intent classification; begin current-app observation, exact
window capture, browser attach, provider warmup, and reversible app-resolution in
parallel; store prepared artifacts under a generation ID; final transcript validates
and adopts or discards them; clarification answers resume the same TaskState; cancellation
releases input and invalidates preparation. Add latency metrics listed in the product brief.

### WHAT NOT TO TOUCH
No mutation before reliable final transcript. Do not redesign the notch visuals beyond
states needed to communicate listening/preparing/acting/verifying/question.

### NO-HARDCODING REQUIREMENTS
Delete flight-specific preparation. Preparation is capability-based, not task-name based.

### REAL UI TEST TO PERFORM
Physically hold the real hotkey, speak one native and one browser request, observe partial
text/preparing state, release to act, then interrupt one run. Also submit typed input and
answer one clarification.

### AUTOMATED TESTS
Revised partials, generation invalidation, no early mutation, artifact adoption/discard,
cancellation cleanup, task-ID continuity, and milestone ordering.

### SUCCESS CONDITION
Speech end-to-first-useful-action improves measurably; zero actions occur from partials.

### COMMIT INSTRUCTIONS
Commit `voice prep`.

### SHORT FINAL REPORT FORMAT
`Commit / overlapped work / latency before-after / tests / real UI evidence / blocker`.

---

## Worker 8 — remove demo hardcoding and run the flight torture test

### GOAL
Delete task-specific planner/controller branches only after the general runtime can
handle their behavior, then use flights as a torture test rather than a profile.

### WHY IT MATTERS
Current flight logic spans routing, URL planning, date filling, evidence extraction,
speech preparation, and summarization; it can make one demo pass while hiding weak
fundamentals.

### CURRENT STATE
Flight-specific code is in `plan.py`, `controller.py`, `request_routing.py`,
`partial_intent.py`, `summarize.py`, and tests. Calculator instructions/compiler are in
`tasks.py`; “new note” intent is in `native_apps.py`.

### EXACT FILES / FUNCTIONS TO INSPECT
All locations above plus `reasoning/intent.py`, `reasoning/reasoners.py`, task policy,
date normalization, and flight/request-routing/verification tests.

### WHAT TO IMPLEMENT
General slot extraction and clarification; deterministic date normalization service;
general `OPEN_URL`, `ENTER_TEXT`, `SELECT`, `PRESS`, `WAIT_FOR`, `VERIFY`, `ASK_USER`,
and `REPLAN`; structured result extraction/summarization; semantic expression entry as
an executor capability if retained. Migrate tests to capability behavior, then delete
the flight URL/evidence/prompt branches, note-specific parser intent, and Calculator
planner instructions.

### WHAT NOT TO TOUCH
Do not weaken safety or remove useful deterministic primitives. Do not replace regex
hardcoding with domain-named prompt examples or fixed selectors.

### NO-HARDCODING REQUIREMENTS
Search the full repository for flight/airport/site/app/demo terms in runtime code. Any
remaining occurrence needs a general-capability justification or must be test/docs only.

### REAL UI TEST TO PERFORM
Through the real popup: `Find flights to San Francisco next week` must ask origin; reply
with origin and exact dates; task resumes without restart; interact with real Google
Flights autocomplete/dynamic controls; stop before booking; verify route/dates/results.
Then run one unrelated travel site or dynamic form without code changes.

### AUTOMATED TESTS
Parameterized missing slots for non-flight domains, relative dates, autocomplete,
repeated controls, stale rerender, async completion, and hardcode audit assertions.

### SUCCESS CONDITION
The flight test passes through only general capabilities, and no production branch asks
whether the task is a flight, Calculator, or note.

### COMMIT INSTRUCTIONS
Use small commits such as `slot clarify`, `date facts`, `flight purge`, `flight fix`.

### SHORT FINAL REPORT FORMAT
`Commits / deleted special cases / generalized capabilities / tests / real UI trace / remaining exceptions`.

---

## Worker 9 — MVP release gate and final QA

### GOAL
Prove the public MVP through the real installed product and block release on false claims.

### WHY IT MATTERS
Unit/mocked/toy-browser success did not catch the Calculator failure.

### CURRENT STATE
The repository has broad automated coverage, Studio/replay, interaction metrics, browser
probes, and native test helpers, but no enforced real-product matrix.

### EXACT FILES / FUNCTIONS TO INSPECT
`bench/`; `real_tasks.py`; `interaction_metrics.py`; `tracing.py`; `studio.py`;
`scripts/build_macos_app.py`; all live/native/browser smoke tests; this plan.

### WHAT TO IMPLEMENT
A release checklist/runner that records build SHA, permissions, provider roles, task ID,
latency breakdown, model calls, context bytes, browser protocol calls, retries, focus
changes, execution paths, verification outcomes, screenshots, and replay bundle. Keep
personal data out. Automated/live results must be separate.

### WHAT NOT TO TOUCH
No feature work except minimal reproducibility fixes. Do not waive a failed category or
replace it with a mock.

### NO-HARDCODING REQUIREMENTS
Test descriptions may name scenarios; runtime code may not gain scenario branches.

### REAL UI TEST TO PERFORM
On `/Applications/MCP-Vision.app`: (1) Calculator arithmetic, (2) create and populate a
TextEdit/Notes item, (3) real-browser research/navigation, (4) clarification+resume,
(5) explain a visible error/screen, (6) one background action. Physically use hotkey and
voice. Verify each final state independently and confirm mouse/focus behavior.

### AUTOMATED TESTS
Full suite, browser integration suite, package/install smoke, replay schema validation,
hardcode audit, and release report validation.

### SUCCESS CONDITION
All six categories pass automated checks and real UI dogfood on the same commit; every
state change has delivery and semantic verification evidence; failures are reproducible.

### COMMIT INSTRUCTIONS
Commit harness as `release gate`; commit only necessary fixes separately (`ui polish`,
`tests`, etc.). Do not squash.

### SHORT FINAL REPORT FORMAT
`Build SHA / six-category table / latency table / test commands / artifacts / blockers / go-no-go`.

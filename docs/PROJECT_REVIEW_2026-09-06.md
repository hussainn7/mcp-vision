> Historical pre-change audit. The selected OSS direction and current implementation status are in [OSS_DIRECTION.md](OSS_DIRECTION.md). Results below describe the earlier checkout, not validation of the new runtime.

**mcp-vision: project audit and product direction — September 6, 2026**

My strongest recommendation is to build **verifiable task packs for computer use**, initially for a narrow business workflow: collecting evidence from client dashboards and producing a reviewable weekly report. Pair each pack with reproducible failure cases. A pack should say which account it uses, what it is allowed to do, what constitutes completion, and what evidence supports the result.

The headline product promise could be: **“Turn a browser task into a result you can check.”** The first user should select a working task, connect a test account, and get an artifact with its sources, missing data, and verification status. They should not have to design an agent architecture or run a large model on their laptop.

This is a hypothesis with good fit to the repository, not an untouched market or a guaranteed growth mechanism. OpenAdapt already overlaps strongly with verified workflow execution. The differentiation must come from a particular audience, useful task packs, onboarding, and a public record of handling failures—not a claim that nobody else verifies actions.

The one engineering change I would make regardless of the eventual market: **make success an explicit, independently checked result, and make “unknown” a valid outcome throughout the system.** Today several components convert missing evidence into apparent success.

**What I inspected and what actually ran**

Reviewed the public onboarding and package configuration, CI, both MCP server paths, the main and alternate agent loops, tool registry, cloud/local model adapters, browser transports, screen perception, grounding, policy/HUD, session and identity state, compression, memory/skills, traces, judging, benchmark generation, and tests. This is a broad engineering/product audit, not exhaustive formal verification or an audit of every historical trace or every YC archive entry. Existing uncommitted changes were included in the review and preserved.

Tests below ran before your instruction to stop local execution. I then terminated the Ollama server and model runner I had started. No further application execution should run on your Mac. A remote execution destination has not yet been supplied; **these are not remote-server results**.

| Check | Observed result | What it establishes |
|---|---|---|
| Full pytest collection | 68 passed, one Pydantic deprecation warning | Current unit tests pass; does not establish browser or task reliability |
| Module self-check runner | 23 passed | Individual modules and scripted demos work in the existing environment |
| Deterministic benchmark | 5/5 passed | The scripted loop handles the five declared scenarios; model and tool results are stubbed |
| Headless Chromium fixture workflows | 3 passed, 2 failed | Actual form entry and modal handling have regressions |
| Wheel build | Succeeded | Archive inspection revealed missing runtime modules and assets |
| Setup check | Initially failed because Ollama was stopped; passed after starting the installed service | Existing qwen3:8b was available; no model download was needed |
| Real-model smoke test | qwen3:8b correctly read a synthetic file and returned “Lighthouse” and 7 open tickets | One real model/tool round trip worked with a read-only allowlist; no desktop/browser success claim |
| Adversarial probes | Confirmed false calendar fallback, unapproved deletion registration, permissive Send/Enter classification, and unsupported success judgments | Existing tests miss product-critical failure modes |

The first Chromium attempt was blocked by the sandbox. Repeating it outside the sandbox produced the actual 3/5 result above. A build initially blocked by network restrictions subsequently succeeded. These environment failures are separate from application defects. The synthetic smoke test used isolated memory and exposed only `read_file`; it did not use personal apps or logged-in sites. No paid model calls were made.

Raw evidence is in [/tmp/mcp-vision-audit](/tmp/mcp-vision-audit), including `self-checks.log`, `e2e-unsandboxed.log`, `build-unsandboxed.log`, `probes.log`, and `live.log`. The latest valid deterministic report is under `state/bench/20260906_211441`. An earlier audit-harness run accidentally retained its temporary specialist override and is invalid; it was corrected before recording the 5/5 result. Benchmark HTML generation was disabled during the audit run to preserve the repository's existing report.

**The codebase has worthwhile foundations**

The specialist allowlists, injectable model/tool interfaces, synthetic browser fixtures, structured identity claims, and trace files are good starting points. The project is small enough to consolidate without an enormous migration. The interesting part is the runtime around execution: task boundaries, observed account state, verification, recovery, and learning from failures. Model-provider adapters and screen clicking alone are much easier for competitors to match.

| Area | Keep | Improve before expanding |
|---|---|---|
| Agent orchestration | Tool allowlists; dependency injection | Mandatory completion predicates; cancellation; consistent errors; move planning into protected trace lifecycle |
| Browser | Native/CDP options; semantic snapshot work | One target identity and action contract shared by both paths |
| Session state | Separate inferred, observed, and verified claims | Bind to session/account/workspace; invalidate on logout/tab/account changes; strengthen evidence provenance |
| Perception | Coordinate helpers and small visual crops | Use accessible names or supported vision input; explicitly handle missing OCR and stale frames |
| Policy | Code-level governor and fail-closed restricted confirmation | Apply one policy to every entry point, file operation, keyboard submission, and fallback |
| Traces | JSONL and escaped HTML viewer | Redaction, retention, complete final export, real model/token/cost metadata |
| Learning | Compact examples and simple retrieval | Only learn independently verified results; parameterize account-specific values; scope memory |
| Evaluation | Reusable fixtures and deterministic harness | Require browser tests in CI; adversarial completion tests; repeated real runs |
| Packaging | Standard Python project and CLI | Build/install outside checkout; include runtime dependencies/assets; one supported entry point |
| Distribution | Existing README/demo | Specific audience and outcome, license file, contributor path, release artifacts, remote demo |

**Release blockers, in priority order**

1. **Calendar failures produce invented personal facts.** [mac_agent.py:86](/Users/h/OllamaTest/mac_agent.py:86) catches errors or empty results and ultimately returns a hardcoded meeting with named attendees. The probe reproduced this without accessing Calendar. Return typed `unavailable`, `empty`, or actual observations. Respect the date query; the implementation currently enumerates all events rather than filtering for “today.” Demo fixtures belong behind explicit demo flags.

2. **Destructive file operations bypass the orchestrator's approval flag.** [tools.py:229](/Users/h/OllamaTest/tools.py:229) marks only two browser fallbacks dangerous, while general/coworker specialists expose `delete_file`. [mac_agent.py:145](/Users/h/OllamaTest/mac_agent.py:145) can recursively remove directories. `write_file` can overwrite existing files and verifies only existence. Add filesystem scope, explicit destructive/overwrite policy, and content-aware verification. Do not fix this with a prompt instruction alone.

3. **Installation does not contain the advertised application.** The built wheel includes `mcp_vision`, `phase1_vision`, and `phase2_mcp`, but omits `config.py`, `mac_agent.py`, `simple_agent.py`, `agent.py`, `specialists.toml`, and the Chrome extension. Several included modules import those missing modules. [pyproject.toml:29](/Users/h/OllamaTest/pyproject.toml:29) also exposes `mac-agent = mac_agent:run`, although that function expects a task argument rather than acting as a no-argument CLI adapter. Consolidate under a real package, include assets, and test installation in a clean environment outside the checkout. Archive inspection confirms the omissions; a clean installed runtime was not executed after the remote-only instruction.

4. **Real browser entry and occlusion tests fail.** `_ax_snapshot` creates numeric records but does not attach the DOM `data-agent-index` attributes that `type_into_index` requires. The AX route also bypasses the DOM occlusion pruning. [playwright_tools.py:574](/Users/h/OllamaTest/phase2_mcp/playwright_tools.py:574) and [playwright_tools.py:799](/Users/h/OllamaTest/phase2_mcp/playwright_tools.py:799) explain the observed form timeout and the supposedly hidden “Save draft” remaining in the snapshot. Retain stable AX backend-node references or consistently map them to DOM handles, then apply reachability checks on both routes.

5. **Default browser success means dispatch, not outcome.** [chrome_native.py:516](/Users/h/OllamaTest/phase2_mcp/chrome_native.py:516) calls `el.click()` and returns success if JavaScript returns `ok`. Typing assigns `.value`, which is incomplete for some controlled inputs/contenteditable widgets. Key presses dispatch synthetic events and report success without checking the result; this does not establish that normal browser default actions occurred. Use native/Playwright input where appropriate and check intended state, with bounded recovery. No full authenticated native-Chrome workflow was validated in this audit.

6. **A rejected answer can become a successful run on the next turn.** In [agent.py:173](/Users/h/OllamaTest/agent.py:173), reflection runs only once and the identity warning is similarly limited by `verify_nudge`. A scripted probe produced an unsupported answer, a rejecting critic, another unsupported answer, and a final passing score of 0.9. Keep a bounded retry budget, but fail or return incomplete when the required evidence remains absent. Also fix `subgoals` being reset after planning and handle planner failures inside the protected lifecycle.

7. **The judge and report can claim more than they measured.** [judge.py:47](/Users/h/OllamaTest/judge.py:47) treats a clean ending as goal success. An empty-action trace claiming “Created it” scored 1.0 in the probe. Optional LLM-judge objections append issues without changing the verdict. [bench/html_report.py:39](/Users/h/OllamaTest/bench/html_report.py:39) embeds fixed capability scores, defaults to a 5/5 result when data is missing, and sets passed golden cases equal to total golden cases without loading replay outcomes. Remove these shortcuts. Every displayed result needs a run ID, revision, model, fixture/task version, and measured verdict.

8. **Policy protection differs by path and misses submission semantics.** The packaged server's classifier allows an element labeled “Send” and the Enter key as routine writes; `press_enter` is not separately passed into classification. The richer agent, older MCP server, and standalone visual agent do not all use that governor. Screenshots/labels can be stale or missing, so keywords cannot establish authorization. Use explicit capabilities and operation semantics; require a fresh, scoped target before committing a write. [server.py:100](/Users/h/OllamaTest/src/mcp_vision/server.py:100), [governor.py](/Users/h/OllamaTest/src/mcp_vision/core/governor.py), [simple_agent.py:72](/Users/h/OllamaTest/simple_agent.py:72).

9. **Gemini errors can include the API key.** [backends.py:365](/Users/h/OllamaTest/backends.py:365) places the key in the request URL; [backends.py:68](/Users/h/OllamaTest/backends.py:68) includes that URL in errors, which flow into printed output and traces. Redact query credentials and sensitive error payloads centrally. This is a code-path finding; I did not expose or test a real key.

10. **The extension relay lacks authenticated, correlated responses.** [chrome_native.py:320](/Users/h/OllamaTest/phase2_mcp/chrome_native.py:320) uses a shared command/result slot, wildcard CORS, and accepts POST data without checking a session secret or command ID. Its lock does not serialize the whole request/response lifecycle. Scope origins, authenticate the extension, validate IDs and deadlines, and separate sessions. Exact website exploitability depends on browser local-network rules; the missing protocol protections are directly visible in code. The non-macOS `connect` command also starts a daemon thread and returns despite telling the user to leave the terminal open.

11. **The installed perception experience is underpowered without OCR.** [parser.py:126](/Users/h/OllamaTest/src/mcp_vision/core/parser.py:126) uses heuristic dark/edge boxes with generic labels when optional OCR is unavailable; [models.py](/Users/h/OllamaTest/src/mcp_vision/core/models.py) excludes the image from serialization. The host may receive unlabeled regions without visual context. This is not equivalent to semantic desktop understanding. Prefer supported accessibility data; expose an image/crop when semantic labels are insufficient. Version snapshots, invalidate them after actions, and qualify Retina/multi-display coordinate behavior.

12. **The learning loop can preserve false successes and sensitive values.** `skills.py` stores exact arguments and declares them proven; its inputs inherit the judge's weaknesses. Identity detection also promotes matching page text/account-menu strings into `VERIFIED`, so that tag is a heuristic observation, not an independent authentication guarantee. Separate account binding, source evidence, task completion, and execution status. Redact stored task data and require independent postconditions before promoting examples into reusable packs.

13. **Onboarding overstates browser behavior and support.** The README says CDP always sets webdriver and causes Google logout. That causal claim is too broad. Chrome documents specific default-profile debugging restrictions; this does not prove every CDP attachment triggers logout. Replace assertions with a tested browser/version/transport matrix. [Chrome documentation](https://developer.chrome.com/blog/remote-debugging-port?hl=en). The repository also needs a license file, contribution guide, release notes, and an accurate remote-browser versus macOS-desktop support table.

14. **CI can go green without testing the released experience.** The pytest job lists only five test files. The browser runner exits successfully when Chromium is unavailable. Require the full suite and expected E2E count in CI, distinguish skip from pass, and add installed-wheel smoke checks. Keep optional developer skips separate from release gates. No new application tests were run after your remote-only request.

**What the market evidence says**

Astra makes a general-purpose intelligence contest a poor fit for this repository. Official documentation lists both computer use and MCP among its supported tools. That also makes it a potential backend for a narrower product. Changing the model name alone will not implement a full native computer-use integration or solve product workflow issues. [Official Astra model documentation](https://developers.openai.com/api/docs/models/gpt-6-astra).

| Competitor / category | Existing position | Implication |
|---|---|---|
| [Browser Use](https://github.com/browser-use/browser-use) | Open-source browser-agent toolkit | Generic browser execution is already an established category |
| [Playwright MCP](https://github.com/microsoft/playwright-mcp) | Structured browser tools, persistent profiles, extension connection to existing sessions | “Use your logged-in browser” is not unique |
| [BrowserOS](https://www.browseros.com/) | Open-source browser with agent workflows and model choice | Local/BYOK browser agents are already consumer-facing products |
| [Agent S](https://github.com/simular-ai/Agent-S) | General computer-use agent framework | Avoid a broad desktop benchmark race without resources and differentiated data |
| [Cua](https://github.com/trycua/cua) | Drivers, cloud/VM environments, evaluation infrastructure | Reuse execution infrastructure; a generic benchmark runner is not a new category |
| [OpenAdapt](https://openadapt.ai/how-it-works) | Demonstration, compiled replay, identity/result checks, verified outcomes | Strongest direct overlap with task packs; verification by itself is not sufficient differentiation |
| [Laminar](https://www.ycombinator.com/launches/NOU-laminar-the-missing-developer-tool-for-browser-agents) | Browser-agent observability | A trace viewer alone is an incremental feature |
| [Skyvern invoice workflow](https://www.skyvern.com/docs/cookbooks/bulk-invoice-downloader) | Portal invoice downloading | Invoice collection is useful but already directly served |

The competitor descriptions are based on their published materials, not independent performance comparisons. Benchmark scores from different environments and dates should not be ranked as if they were equivalent.

There is specific evidence behind the reliability problem. BrowserOS's founders described their one-shot UX as inconsistent and said visual workflow building intimidated new users. One tester found setup more work than doing the task. The useful lesson is to ship ready-to-run outcomes with a small correction surface. This is founder experience, not a population survey. [BrowserOS on Hacker News](https://news.ycombinator.com/item?id=44987221).

Reddit contributes three different signals:

- Local-agent users describe continued supervision and checking as the cost of using agents. This discussion is primarily about coding, so it is adjacent evidence rather than direct demand for your product. [LocalLLaMA discussion](https://www.reddit.com/r/LocalLLaMA/comments/1u6mmuu/local_coding_agents_are_good_now_but_only_if_you/).
- Agency operators discuss manual screenshots, recurring cross-platform reporting, and existing alternatives including Looker Studio and connector products. That validates a workflow worth interviewing people about, while also showing substantial competition. The thread includes vendor promotion, which should not be treated as independent customer testimony. [Agency reporting discussion](https://www.reddit.com/r/agency/comments/1j7cnac/client_reporting/).
- An older accessibility discussion describes a need for patient voice interaction, immediate stopping, approval before posting, and a system the user cannot be expected to debug. Those are concrete product requirements. Its age makes it useful for understanding needs, not measuring today's model capabilities. [Voice-controlled computer discussion](https://www.reddit.com/r/LocalLLaMA/comments/1hovwdd/how_far_are_we_from_having_true_personal/).

The repeated invoice-bot posts found during research were cross-posted promotion, not multiple independent validations. More generally, this is a purposive sample of public discussions; it does not establish market size, willingness to pay, or post-Astra adoption trends.

Your consumer thesis has direct support in YC's Fall 2026 requests: consumer AI, multiplayer AI, and products for older adults are explicitly discussed. But the same page also calls for infrastructure and business systems. This supports several opportunities, not a universal switch away from B2B. [YC Requests for Startups](https://www.ycombinator.com/rfs). A contemporary 2025 recap already discussed internal agent builders alongside consumer ideas, suggesting continuity as well as change; this is a secondary archival source. [Summer 2025 recap](https://www.vccafe.com/requests-for-startups-2025-part-3/). HN comments are community opinions, not official YC investment advice. Private Bookface material was not accessed.

Research also supports measuring more than one successful run: recent work examines stochasticity and task ambiguity, while UI-CUBE focuses on operational reliability beyond task accuracy. Neither establishes that this project's proposed approach will win. [Reliability paper](https://arxiv.org/abs/2604.17849), [UI-CUBE](https://arxiv.org/abs/2511.17131).

**Three viable directions**

| Direction | First user and job | Fit today | Growth / revenue hypothesis | Main reason it could fail |
|---|---|---|---|---|
| **1. Verifiable task packs — recommended** | Small agency operator collecting evidence from several client dashboards | Best use of session state, browser tools, reports, and traces; still needs substantial hardening | Open packs attract builders; hosted execution and maintained workflows sell to teams | Existing reporting tools already solve most of a customer's problem |
| **2. Computer-use failure lab** | Developers testing wrong-account actions, stale targets, fake success, duplicate retries | Best immediate fit to tests and trace infrastructure | Useful OSS fixtures and reproducible bug reports; later hosted CI/regression testing | Cua/HUD and observability tools overlap; interest may not turn into paid use |
| **3. Patient computer copilot** | Users who need guided, interruptible assistance with recurring browser tasks | Some fit to HUD/guide_user; voice and accessible interaction are largely new work | Consumer emotional value and community distribution; possible assisted onboarding | Support burden, accessibility testing, retention, and entrenched OS/platform offerings |

Choose direction 1 if building a useful business is the priority. Use a tightly scoped part of direction 2 as its open-source contribution engine, not as a second product. Choose direction 3 only if you have direct access to the intended users and want to commit to their needs; do not treat accessibility as a marketing hook.

**A concrete first product**

Start with one agency workflow whose data cannot already be obtained satisfactorily from their existing connectors: **“Prepare this client's weekly evidence brief from these two approved dashboards.”** Interview agencies first; if connector-based reporting already handles their needs, do not force browser automation into it.

The brief should contain a client/account identifier, time range, extracted metrics, source URLs, timestamps, evidence snippets, missing fields, and a reviewable narrative. The same values should be available as structured JSON. It stays a draft. The initial pack has no budget-edit, publish, delete, or outbound-message capability.

The interesting demo is a wrong-account interruption: run a synthetic client report, switch the dashboard account midway, and show the pack stop with an explicit account mismatch instead of generating a plausible report. Then correct the context, resume from a valid checkpoint, and inspect every number's source. This is a proposed demo and build target, not current behavior verified in the application.

A pack needs more than a prompt:

| Pack field | Purpose |
|---|---|
| Task/version and parameters | Repeat the same job without copying hardcoded client values |
| Allowed origins and account/workspace binding | Prevent mixing customers and contexts |
| Allowed operations and output directory | Make authorization inspectable and enforceable |
| Preconditions | Check sign-in, account, time range, and expected app state |
| Completion predicates | Define what must be true before success |
| Evidence schema | Link every result to an observation or artifact |
| Recovery/checkpoint rules | Stop, resume, or retry without accidental duplication |
| Model policy and resource budget | Cloud inference by default for the hosted demo; optional self-hosted model later |
| Failure fixtures | Wrong account, expired session, shifted UI, empty data, false success |

This is not a claim of cryptographic proof from screenshots. For a read workflow, evidence can make a claim inspectable; independent export/API/file checks strengthen it. For a write workflow, a generic page change is insufficient—read back the specific record and its expected values through a separate observation where possible.

For the first release, put model calls and browser execution on a server, with an isolated browser profile per workspace. Let the user authenticate interactively in that remote session. A hosted Linux browser cannot operate the user's local Apple Notes or reuse their local Chrome session automatically. Native macOS tasks require a separate Mac executor; defer them from the hosted MVP. A cloud model option alone does not move browser execution off the Mac, and `guide_user`/micro-vision currently still contain local-model paths.

**Execution and launch plan**

Treat the schedule as a planning estimate for a focused maintainer, not a promise.

1. **First week: establish truth and installability.** Fix fabricated data, destructive policy, missing wheel contents, browser regression failures, and false-success grading. Remove static scores from the report. Make one shared action/result contract. Run full tests and clean-wheel checks on server CI. Do not add providers, specialists, or a broad UI redesign yet.
2. **Next two weeks: build one pack and an online test drive.** Use a synthetic two-client dashboard with known ground truth, remote browser execution, cloud model calls, an account-change interruption, and an evidence artifact. Give beginners a working example without model downloads, personal accounts, or terminal configuration. Add streaming progress, Stop, and a clear Needs help state.
3. **Following two weeks: validate repeat use with five operators.** Observe their actual last report and tool setup; ask what they copied by hand and why an API/connector did not solve it. Run repeated trials across their approved accounts. Add a second pack only when the first is reused without you driving it.

Proposed decision gates, not measured results:

- At least 4 of 5 testers finish the synthetic first task without maintainer help.
- At least 3 of 5 design partners voluntarily reuse the real pack for four reporting cycles.
- Net time saved remains positive after counting review, correction, setup, and maintenance.
- Report verified completion, safe stops, wrong results, interventions, latency, and cost separately. Never count a safe stop as task completion.
- Track silent wrong results explicitly. Zero observed failures in a small sample does not prove zero risk.
- Stop or change the agency angle if existing connectors handle the work, people do not return, or review takes as long as the manual task.

For open-source traction, launch with one outcome and one reproducible failure story. The first minute of the README should show the input, the result, the interruption, and how another person can reproduce it. Add an actual license file and contribution instructions. Give contributors bounded work: one fixture, one adapter, one verifier, or one task pack. Build a shared suite where every accepted pack brings its own failure tests.

The public GitHub page currently shows no About description, website, or topics; these are inexpensive discovery improvements after the promise is clear. Indexed star counts are tiny and can lag, so do not read them as demand research. [Repository page](https://github.com/hussainn7/mcp-vision).

Use Show HN for a runnable engineering story, LocalLLaMA for genuinely supported self-hosted inference, and agency communities for the actual reporting problem. Follow their rules and disclose authorship. Publish measured comparisons and failures, not selected “agent did everything” clips. No outreach or public changes were made during this review.

Keep the runtime, pack format, starter packs, and failure fixtures open. Potential paid value is reliable hosting, schedules, team review, maintained workflows, and support. Test willingness to pay for the saved work before choosing a price. Track activated users, weekly successful jobs, repeat pack usage, outside contributions, and retained paid pilots alongside stars.

**Decision**

The project is a promising prototype with multiple competing execution paths and overstated reliability. Its strongest next step is a smaller, truthful product: one account-aware task pack, a useful artifact, measurable completion, and a remote test drive. Better foundation models then improve your product instead of eliminating its entire reason to exist.

There is no single feature that can guarantee traction. The most defensible immediate commitment is to eliminate false success, then prove that a specific group repeatedly gets valuable work done through the same pack. Further runtime tests require your chosen remote host or CI environment; none were dispatched remotely in this audit.

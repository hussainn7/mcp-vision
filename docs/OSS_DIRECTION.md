# OSS direction: model choice with observable execution

Decision: pursue this as an open-source developer runtime, with a measured validation phase. There is a useful problem here; research does not establish that this specific implementation will win adoption. Keep the audience broad across computer-use developers. Lead with “Your model. Your computer. Evidence for every action.”

This updates the earlier project audit to match the chosen runtime direction. It is not a promise that a single feature guarantees traction.

## What the market actually says

Model independence, MCP, local execution, and accessibility are already competitive requirements. [Cua](https://cua.ai/) offers an open-source driver, MCP/CLI access, accessibility, and broader computer infrastructure. [Playwright MCP](https://github.com/microsoft/playwright-mcp) is an established browser tool interface. [Browser Use](https://github.com/browser-use/browser-use) addresses agent-driven browser work, while [BrowserOS](https://www.browseros.com/) targets the browser experience. Therefore “API + local” is a sound architecture but a weak standalone reason to switch.

The most useful qualitative signal is frustration with unreliable or expensive execution. A [Reddit discussion about moving off browser-use](https://www.reddit.com/r/LocalLLaMA/comments/1rm5mkv/anyone_moved_off_browseruse_for_production_web/) describes slow multi-step browsing and repeated model calls. Another [discussion of current browser agents](https://www.reddit.com/r/LocalLLaMA/comments/1uh0uz7/whats_the_latest_on_agent_browser_use/) contrasts autonomous agents with deterministic Playwright workflows. These are anecdotes from self-selected users, with promotional replies mixed in; they are hypotheses to test, not market-size data.

The [BrowserOS Hacker News launch discussion](https://news.ycombinator.com/item?id=44987221) is useful context for local-model interest and the difficulty of workflow setup. [Workflow Use's launch](https://news.ycombinator.com/item?id=44007065) also shows that deterministic reuse is an existing direction, not unexplored territory.

[YC's Fall 2026 requests](https://www.ycombinator.com/rfs) include consumer AI, small-software infrastructure, and multiplayer AI. This supports exploring new user experiences; it does not show that B2B is over or that an OSS runtime should become a consumer app. The relevant inference is to reduce setup friction and make long-running work understandable to people. Do not use a YC thesis as product-market-fit evidence.

## Three options

| Option | Why users might adopt | Main difficulty | Decision |
|---|---|---|---|
| Small model-neutral runtime with action evidence | Add computer access to an existing agent without adopting another planner; debug failures concretely | Strong competition; must prove easier integration and fewer silent failures | Build now |
| Portable workflow recordings with explicit checks | Reuse successful work across models and inspect why replay failed | Already overlaps workflow/replay products; needs robust runtime and parameterization | Next, after runtime validation |
| Consumer “agent for everything” app | A direct way for nondevelopers to try automation | Distribution, trust, OS permissions, and broad task reliability all become your responsibility | Defer as a separate product |

The strongest immediate investment is **a runnable demo plus regression-backed action receipts**. Show a normal workflow, a moved target, a blocked submission, and an explicit successful postcondition. Let developers inspect the receipt. A truthful, reproducible failure demonstration can be more convincing than another polished “agent clicked a website” video.

## What this implementation establishes

The new MCP browser path is model-free and isolated. It binds actions to a snapshot and exact target, revalidates after confirmation, defaults input off, and exposes screenshots. Clicks return unverified receipts; field read-back and text checks report specific evidence. Ambiguous dispatch is represented rather than automatically retried. The demo uses a disposable page without credentials or model downloads.

The packaging, documentation, license, and hosted test workflow are part of the product, not cleanup to postpone. A developer who cannot install the wheel or connect an MCP host cannot evaluate the idea.

This is still a preview. Main-document DOM controls are not full OS accessibility, arbitrary websites are not atomic, and keyword policy cannot infer every side effect. The current work does not establish cross-platform native reliability, whole-task success, or comparative superiority. The new runtime tests have been authored but require hosted execution.

## Adoption experiment

After CI passes, invite ten independent developers to integrate it with their existing host/model. Use public demo fixtures and one task they choose. Offer broad examples—browser data entry, inspecting a website, preparing a draft—without committing the project to an industry niche.

Record, with consent: install-to-first-receipt time; whether setup required direct maintainer help; task predicate success; false completion claims; stale-target stops; recovery time; and whether the developer returns within a week. Keep the runtime/model/provider versions with each result. Do not collect screenshots or task content by default.

Suggested decision thresholds (targets, not measured results): at least eight of ten complete the demo unaided; at least five try their own integration; at least three return the next week. Zero false task-completion claims is a contract requirement, not a percentage to average away. If people install successfully but do not return, interview them before expanding the feature surface.

Launch with a short terminal-to-result recording, an honest limitations table, and a reproducible regression suite. Publish comparisons only after running the same tasks with independent postconditions on the named versions of competing runtimes. Stars and views are secondary to repeat use and external integrations.

## Release gate

1. Hosted unit, browser, demo, benchmark, and clean-wheel MCP checks pass on the actual commit.
2. A real MCP host works with both a cloud-backed and a local-model-backed configuration; report exact configurations tested. The no-model demo is not a substitute for these checks.
3. Native OS support is labeled experimental until validated on dedicated test machines.
4. No advertised performance number without an attached run and explicit task oracle.

The current blocker is execution infrastructure: no project test server has been designated, Codespaces scope is unavailable, and the repository has no listed self-hosted runners. The user will push the stacked commits, at which point the included GitHub-hosted workflow can execute. No new runtime validation has been run on the user's Mac.

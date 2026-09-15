# Replace one-shot ask with a bounded browser controller

`ask` previously treated a sufficiently long first snapshot as success. It now compiles a read-only Mission, observes page evidence, follows relevant observed links or scrolls, verifies each action on the next snapshot, and evaluates the actual question before producing an answer. The limit is ten observation steps, with at most two retries after an unsuccessful action. Unknown execution always leads to another observation. Authentication, policy denial, missing evidence, and the cap produce an explicit incomplete result.

Personal product routing precedes model routing; a deep repository tab is not reused for a personal account question. Research still uses Google. Link destinations come from the snapshot rather than invented account paths. Writes stay disabled and the deny confirmer is unchanged. Existing interactive CAPTCHA handling remains the default for other runtime users; ask returns the blocker without waiting in a dialog.

Run the north-star check:

```sh
mcp-vision ask "how many commits did I do today on github"
```

Use `--model none` for deterministic offline evaluation. Counts require explicit metric and requested-period evidence plus account context; contributions never substitute for commits. The generic planner follows relevant links and scrolls. For questions outside the conservative local evaluator, the configured model can evaluate exact page excerpts against the Mission. Missing controls, unsupported page layouts, and insufficient account context return incomplete instead of guessing. No application forms are filled or submitted.

Validation:

- Full suite: 149 passed, 18 skipped (local socket access required by studio tests).
- Final focused controller, ask, mission, and governor suite: 45 passed.
- Dry benchmark: 5/5 passed.
- Live GitHub with `--model none`: stopped after bounded attempts with “No observed progress after two retries; required evidence is missing.” No count claimed.
- Live “what is kubernetes” with `--model none`: returned observed definition text and Google search URL. The result prompted an additional change to return only the first matching definition.
- Live Gmail smoke: automatic approval review rejected private inbox access; awaiting user approval. Mock Gmail routing and read-only controller tests pass.
- Setup check: Python/imports/AppleScript available; Ollama connection unavailable in the restricted setup check. Model evaluation was not live-validated.

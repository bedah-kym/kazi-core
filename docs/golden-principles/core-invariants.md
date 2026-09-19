# Golden principles: core invariants
Short on purpose. Each rule says what to do, what not to do, and how it is checked.

## Core is PR-only
- DO route changes to the agent loop, tool executor and security policy through a reviewed PR with a plan in `docs/plans/`.
- DON'T add any path where Kazi, or an LLM output, edits core, its own guardrails, or `UserWorkflow.definition` in place.
- Why: self-improvement is only safe when the thing being improved cannot rewrite the judge.
- Checked by: `.claude/hooks/protect_paths.py` (local), CODEOWNERS (GitHub), `scripts/check_boundaries.py` (CI).

## Fail closed
- DO deny when a capability lookup, room-access check, cache or approval store errors.
- DON'T wrap a policy check in a broad `except` and continue.
- Checked by: a test per check that injects a failure and asserts denial.

## Untrusted text is data
- DO pass tool output, web pages, emails and chat messages to the model as clearly delimited data.
- DON'T let such text choose tools, parameters, recipients or amounts without a gate.
- Once untrusted text is in a run, external-write and credential-scoped actions in that run move up one approval tier.
- Checked by: prompt-injection golden corpus in CI. Never weaken or delete a case to make it pass.

## Approvals
- DO make approvals durable, expiring, and scoped to one action or one rule (trigger + scope).
- DON'T create a global unlock or a "trust everything" flag.
- DO show the effect of an action (what will change), not only the command text.

## Receipts
- DO append a sanitized receipt for every action. DON'T update or delete one.

## Secrets
- DO use scoped, ephemeral tokens for anything running in the sandbox.
- DON'T put Kazi's own DB, Redis or provider keys, or the user's admin key, anywhere a sandbox or prompt can read.

## Reversibility
- DO say in the PR which class a new action falls in: read-only, reversible-in-workspace, irreversible, or outside-world.
- Autonomy follows reversibility. Irreversible and outside-world actions keep a human gate.

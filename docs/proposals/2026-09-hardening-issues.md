# Proposed issues: more reach, same blast radius (v0.6 to v0.7)
Issue-ready. Each has an executable acceptance check. Basis labels: [Analysis] = design judgment, not a verified fact.
Numbers refer to existing issues (#125-#160). Title tags follow the existing `[v0.x][Pn]` convention.
Filed 2026-09-19: N1–N6 are now issues #166–#171; the four amendments below are comments on #134, #126, #135, #127.

## Amendments to existing issues (comment, do not open new)
- **#134 (network on/off + allowlist)** [Analysis] — posted
  - Default `network=off` in every profile. Allowlist only when a task needs it.
  - Do not hand-write an egress proxy. Use a stock, widely used one. A custom proxy is the layer most likely to fail.
  - Rule: no credentials for an allowlisted host may exist inside the sandbox. An allowed domain is still an exfiltration path.
  - Acceptance: test that a command cannot reach a non-allowlisted host, including by DNS.
- **#126 (credential scoping)** [Analysis] — posted: add "the agent loop stays outside the sandbox; only command execution runs inside".
- **#135 (workspace snapshot/rollback)** [Analysis] — posted: state that a snapshotted workspace is what lets in-workspace commands run without a prompt.
- **#127 (injection corpus)** — posted: add cases where the injected text arrives in shell output and in a file inside the workspace.

## New issues
### N1 (v0.6 · P2) Approval telemetry per rule — filed as #166
- Context: approvals that are approved almost every time stop being oversight. Measure it. Depends on #153.
- Build: nightly rollup of approve / reject / expire counts and median latency per (connector, action, risk_level, rule).
- Surface in the daily digest. When a rule is approved on nearly every request over a meaningful sample, offer to promote it to a scoped rule (#160). Starting thresholds (tune): 95% over 50 requests.
- Acceptance: unit test on synthetic approvals; no user content in the metrics.

### N2 (v0.6 · P3) Connector `preview()` (optional dry run) — filed as #168
- Build: optional method returning `{"effects": [...]}` without committing. Approval card shows effects, not raw parameters.
- Additive to the connector-execution contract. Connectors without `preview()` keep working.
- Acceptance: contract doc updated (minor version); golden test of an approval payload containing `effects`.

### N3 (v0.6 · P3) Untrusted-context taint flag — filed as #170 *(touches core: human PR only)*
- Build: the agent loop marks a run tainted once output from an untrusted source (shell, web, inbound message) enters context. In a tainted run, external-write and credential-scoped actions require a durable approval one tier up. A stored rule cannot bypass it.
- Acceptance: extended injection corpus (#127); a test showing a stored "permission once" rule does not bypass the gate in a tainted run.

### N4 (v0.7 · W-A2) Workflow capability manifest + `capability_delta` — filed as #167
- Depends on #155. `definition.capabilities` lists what a workflow may call. The executor enforces it with a deterministic matcher, not an LLM.
- A suggestion's `capability_delta` is computed by code. Delta of zero: fast approval. Delta above zero: escalation approval kind.
- Acceptance: a reviewer suggestion that adds a connector action is classified as widening; a prompt-only change is not.

### N5 (v0.7 · W-D2) Shadow replay before a suggestion is shown — filed as #169
- Build: run vN+1 against recorded inputs of the last K replay-safe executions with side-effect steps stubbed. Store `shadow_result`. Suggestions that regress the cited metric or any assertion are auto-rejected and logged.
- K is explicit: K = 10, tunable via a versioned `shadow_replay_window` config.
- The reviewer model must differ from the model that authored the workflow (config). The reviewer receives typed metrics only, no raw tool output.
- Acceptance: mocked LLM; regressing suggestion never reaches the human; passing suggestion carries its shadow diff.

### N6 (v0.6 · P0-ext) Scenario packs + provider continuity test — filed as #171
- Build: each capability ships at least N scenarios (goal, fixture, verifier script). `kazi eval` runs them and records pass rate and cost per model tier.
- N is explicit: N = 3, tunable via a versioned `scenario_pack_min_size` config.
- Use the results, not intuition, to choose defaults in the model catalog and per-room selector.
- Add one multi-step tool-call fixture per provider adapter (DeepSeek, Claude) checking coherence within a turn and reset on a new user message. Check against each provider's current docs first.
- Acceptance: CI runs a small smoke pack with a mocked LLM; a nightly job runs the full pack.

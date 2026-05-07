# Changelog

All notable changes to **Kazi Core** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`docs/eval.md`** [v0.4 M4-1] — promotes the previously-loose golden
  scenario evaluator to a documented contributor surface. Covers
  scenario format, how to add new scenarios, how to run locally with /
  without an LLM, and how the CI job is wired.
- **CI eval smoke test** — `.github/workflows/main.yml` runs
  `python Backend/manage.py run_golden_eval` on every PR as an advisory
  job. Without `ANTHROPIC_API_KEY` configured, LLM-backed scenarios
  skip; the job still proves the harness wiring is healthy. Flip to
  blocking once planner-regression coverage matures.
- **Five workflow-organized docs pages** [v0.4 M5-4 + M5-5]:
  - `docs/run-locally.md` — boot in <10 minutes, no real API keys
  - `docs/add-a-connector.md` — one file plus one test
  - `docs/add-a-workflow.md` — author the JSON; let the runtime do
    the rest
  - `docs/operate-a-workflow.md` — approve, reject, rerun, replay
  - `docs/deploy-safely.md` — production checklist
  README's docs section is reorganized to surface these in order, with
  the existing component-oriented pages demoted to "reference
  material" below them.
- **`docs/contracts/`** [v0.4 M2-2] — five stable runtime contracts at
  v1.0: connector execution, tool schema, approval, execution detail,
  replay safety. Each carries a `Major.Minor` version, a documented
  shape, examples, common mistakes, and a "changes since" log. New
  contracts must be added with the runtime change that introduces
  them, not in a separate doc PR.
- **`validate_catalog_entry()`** in
  `Backend/orchestration/contracts.py` — runtime validator for the
  tool-schema contract. Wired into `connector_registry`: every
  catalog entry is checked at registration time; bad entries are
  skipped with a warning so a single typo does not break boot.
- **Contract version constants** in `contracts.py` for each contract
  (`CONNECTOR_EXECUTION_CONTRACT_VERSION`, etc.). Pin against these
  to declare what surface your code expects.
- **`Backend/orchestration/test_contracts.py`** — version-pin test +
  twelve validator cases (good entry, optional fields, every error
  branch).
- **`kazi_trace` management command** [v0.4 M4-2] —
  `python Backend/manage.py kazi_trace <execution_id>` renders a
  human-readable timeline for a single workflow execution: header
  (status, timing, result/failure summary), per-step status in
  declared order, every approval cycle (who decided, when, with what
  note), watchdog deferred-run history, receipt count. `--json` emits
  the same data as a parseable document for piping into other tools.
  Documented at `docs/trace.md`.
- **Top-level `examples/` directory** [v0.4 M5-3]. The canonical
  "copy this to start" connector lives at `examples/connectors/echo/` and
  is auto-discovered by the runtime when demo mode is on. No more
  hidden-behind-a-flag template buried inside `Backend/`.
- **`KAZI_DEMO_MODE` runtime flag** [v0.4 M5-2]. Setting
  `KAZI_DEMO_MODE=true` enables example connectors at boot, no real
  credentials required. A loud banner is logged so demo data is never
  confused with real data. See `docs/demo-mode.md`.
- **Demo workflow pack** [v0.4 M5-1] under
  `examples/workflows/follow_up_email/`. A two-step workflow that
  exercises the human-gated runtime end-to-end: step 1 runs
  automatically, step 2 pauses durably for human approval, and the
  replay-safety guard refuses to rerun the unsafe step without
  `force=true`. Uses the `echo` example connector so the loop runs
  with no real credentials when `KAZI_DEMO_MODE=true`.
- **`scripts/demo.sh`** — single-command driver that boots the stack
  in demo mode, migrates, seeds the demo user + workflow, and prints
  the curl commands needed to drive the human-gated loop.
- **`seed_demo_workflow` management command** — loads
  `examples/workflows/follow_up_email/workflow.json` into a
  `UserWorkflow` row. Idempotent.

### Changed

- **`Backend/orchestration/mcp_router.py`** [v0.4 M2-3 phase 1] —
  carries a deprecation note signaling the v0.5 file rename to
  `tool_router.py` (the "MCP" name predates Anthropic's now-standard
  Model Context Protocol) and the planned per-file extraction of the
  six inline connectors. No behavior change — runtime imports keep
  working. Agents and contributors should now expect `tool_router.py`
  as the canonical name in v0.5.
- **`AGENTS.md`** [v0.4 M5-6] — the "read this first" pointer no
  longer recommends `mcp_router.py` over `agent_loop.py`. Repo map
  updated to reflect the v0.4 shape (contracts/, examples/, scripts/,
  eval/, kazi_trace, seed_demo_workflow). New "Demo mode (recommended
  for AI agents exploring the repo)" entry in §3 promotes
  `bash scripts/demo.sh` as the boot command for first contact.
  Connector-add guidance now references the v0.4 contracts and the
  connector freeze.
- `Backend/orchestration/connectors/example_connector.py` removed; the
  echo connector now lives at `examples/connectors/echo/echo_connector.py`.
- `KAZI_ENABLE_EXAMPLE_CONNECTOR` is gone — `KAZI_DEMO_MODE` replaces it
  with a broader scope and a single flag for everything demo-related.
- **Lint posture tightened — phase 1 of M6-1**:
  - **Critical correctness set expanded** in
    `.github/workflows/main.yml`. Now blocks on `E712` (`== True`),
    `E722` (bare `except:`), `E731` (lambda assignment) in addition to
    the previous `E9 / F63 / F7 / F82 / F811 / F202` set. All real-bug
    findings under those rules are clean across `Backend/`.
  - **Whitespace / indentation cleanup** swept across the codebase via
    autopep8 (`W291 W292 W293 W391 W191 E101 E111 E117 E225 E231 E261
    E265 E301 E302 E305 E306 E114 E122 E127 E128 E129 E251 E203 E303`).
    Advisory PEP 8 findings dropped from ~2,461 to ~487 — a ~80%
    reduction. The remaining findings (E501 long lines, F401 unused
    imports, F841 unused locals, C901 complexity) need per-file
    judgement and are the focus of v0.5 phase 2.
  - **Real-bug fixes**: `temporal_integration.py` had two genuine
    Python correctness bugs surfaced by the broader scan — F821
    (undefined `exc` after the binding was cleared by Python's
    except-block scoping rules) and F823 (referenced-before-assignment
    on `runtime_state` shadowed by the inner closure). Both fixed.

### Planning

- **v0.4 brief and roadmap landed** as `docs/v0.4-brief.md` and
  `docs/v0.4-roadmap.md`. v0.4 is the *human-gated runtime cycle*:
  durable approval checkpoints, execution detail records on
  `WorkflowExecution`, operator controls (approve / reject / cancel /
  rerun / pause / resume), a deferred-run watchdog, and a demo
  workflow pack that runs the full
  `request → workflow → approval → receipt → replay` loop with no real
  API keys. A connector and domain-feature freeze is in effect for the
  duration of the cycle. Carryover from v0.3 (strict PEP 8,
  bandit-medium cleanup) is tracked under Milestone 6 of the roadmap.

## [0.3.0] - 2026-05-03

Repository hardening release. No runtime feature changes — focus is on making
the project safe to onboard contributors and AI coding agents onto.

### Added
- `AGENTS.md` at repo root: structured guide for AI coding agents
  (Claude Code, Codex CLI, Cursor, Cline, Aider, Copilot) covering project
  identity, repo map, setup, code style, common change patterns, testing,
  commit conventions, and security boundaries.
- CI runs against a real **Postgres 15** service and a **Redis 7** service —
  no longer SQLite-only. Tests now exercise the same database engine as
  production.
- CI build matrix across **Python 3.11 and 3.12**.
- Test coverage reporting via `coverage.py` with Codecov uploads on the
  primary Python version.
- Container release now publishes **SBOM** and **SLSA provenance**
  attestations and is signed with **cosign** (keyless via Sigstore OIDC).
- This `CHANGELOG.md` (Keep a Changelog format).

### Fixed
- Cross-cutting tests under `Backend/tests/` are now discovered by
  `manage.py test`. Previously, `Backend/tests/` was a loose folder, not
  a Django app, so `test_agentic.py`, `test_agentic_scenarios.py`, and the
  other public agentic tests were silently skipped in CI. Registered as
  the `tests` Django app.
- Updated `CONTRIBUTING.md` and the PR template to reference `AGENTS.md`
  instead of the orphan, git-ignored `agents.md`.
- Removed `agents.md` from `.gitignore` so the agent guide can actually be
  committed.
- **`chatbot/consumers.py` — `ChatConsumer.connect`/`schedule_idle_nudge_if_needed`**:
  the presence-broadcast block (steps 8–11: group_send presence_update,
  build presence snapshot, send snapshot to newly connected user) had been
  misplaced into `schedule_idle_nudge_if_needed`, where its references to
  `current_time`, `current_chat`, `redis`, and `key` were undefined. The
  function crashed on every successful idle-nudge schedule, and new
  connections never got a real-time presence snapshot. Moved the block
  back to `connect()` after `await self.accept()` where every variable
  resolves correctly.
- **`workflows/temporal_integration.py`** — the closure inside the
  `except Exception as exc:` handler referenced `exc` after Python had
  already cleared the binding. Captured the message into a stable
  `error_message` local before defining the closure.
- **`orchestration/connector_registry.py`** — removed an unused
  `_registered_catalog_entries` from a `global` declaration where it was
  never assigned (kept the legitimate use in `reset_registry`).
- Removed two stray `nonlocal summary_text_for_cache, should_cache_summary`
  declarations in `consumers.py` where the names were only read, never
  rebound.

### Operational notes
- Local Python 3.14 is not supported by all dependencies (e.g. some
  Twisted/Django stubs). The supported floor remains **3.11**, with 3.12
  validated in CI. Use the published Docker image or the supported floor
  for development.
- **Lint posture is two-tier**:
  1. *Critical correctness* (`E9,F63,F7,F82,F811,F202`) — strictly
     enforced in CI. Catches syntax errors, undefined names, redefined
     functions, and similar real bugs.
  2. *Full PEP 8* (whitespace, blank lines, line length, unused imports,
     complexity) — runs in CI as advisory only (`continue-on-error`).
  This reflects reality: a large existing codebase carries cosmetic
  drift. Tightening to strict-PEP-8 is a planned **v0.4** task; the
  `.flake8` config defines the per-file exclusions that will be ratcheted
  down over time. New code should target full PEP 8 even though CI
  doesn't fail on it yet.
- **Bandit posture is two-tier** (same shape):
  1. *High-severity* — strictly enforced in CI (`-lll`). Real exploit
     vectors fail the build.
  2. *Medium + Low* — runs in CI as advisory only. Mostly false-positive
     low-severity findings (hardcoded URLs flagged as passwords,
     try/except/pass patterns), plus some legitimate medium ones (e.g.
     `requests` calls without timeouts) that are queued for v0.4.

## [0.2.0] - 2026-04 to 2026-05

The full orchestration core was opened.

### Added
- **Full orchestration core open-sourced**: `workflow_planner.py`,
  `manager_verifier.py`, `agent_loop.py`, `tool_executor.py`,
  `action_catalog.py`, `security_policy.py`, `action_receipts.py`,
  `memory_state.py`, and the connector registry. No more gated stubs.
- Connector plugin API with auto-discovery — drop a `BaseConnector`
  subclass into `Backend/orchestration/connectors/` and it registers on
  startup. Pip-installable connectors via the `kazi.connectors` entry point
  are also auto-discovered.
- Sanitized public agentic test coverage in `Backend/tests/test_agentic.py`
  and `Backend/tests/test_agentic_scenarios.py`.
- Public-facing docs: `docs/quickstart.md`, `docs/architecture.md`,
  `docs/writing-a-connector.md`, `docs/connector-api-reference.md`.
- Public repository automation: CodeQL on cron, dependency review on PRs,
  dependabot for pip + GitHub Actions, signed container release on tags,
  issue forms (bug, feature, connector proposal), pull request template,
  `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`.
- README rewritten with concrete value proposition and usage examples.

### Changed
- Project rebranded from Mathia.OS to **Kazi** (Swahili for "work").
  Agent identity is configurable via `KAZI_AGENT_NAME` (default `Kazi`).

[Unreleased]: https://github.com/bedah-kym/kazi-core/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/bedah-kym/kazi-core/releases/tag/v0.3.0
[0.2.0]: https://github.com/bedah-kym/kazi-core/releases/tag/v0.2.0

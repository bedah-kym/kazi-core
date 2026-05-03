# Changelog

All notable changes to **Kazi Core** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

### Operational notes
- Local Python 3.14 is not supported by all dependencies (e.g. some
  Twisted/Django stubs). The supported floor remains **3.11**, with 3.12
  validated in CI. Use the published Docker image or the supported floor
  for development.

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

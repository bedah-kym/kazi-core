# Contributing to Kazi

Thanks for contributing. This guide keeps contributions consistent, reviewable, and safe.

## Before You Start

- Read `README.md` for setup and architecture.
- Read `docs/architecture.md` for orchestration internals and design boundaries.
- Read `docs/writing-a-connector.md` if you're building a connector/plugin.
- Check open issues before starting work.

## Ways to Contribute

- Report bugs
- Propose features
- Improve docs
- Add tests
- Fix performance, reliability, or security issues

## Development Setup

### Docker (recommended)

```bash
docker compose up --build -d db redis web celery_worker celery_beat
docker compose exec web python Backend/manage.py migrate
```

### Local (without Docker)

```bash
python -m venv .venv
# Windows PowerShell
. .venv/Scripts/Activate.ps1
pip install -r requirements.txt
python Backend/manage.py migrate
python Backend/manage.py runserver
```

You need PostgreSQL + Redis running and the env vars from `README.md`.

## Branching and Commits

- Create a feature branch from `main`.
- Use focused commits with clear messages.
- Keep unrelated changes out of the same pull request.

Suggested commit format:

```text
type(scope): short summary
```

Examples:

- `fix(notifications): sync unread count after dismiss`
- `docs(readme): clarify local setup`
- `test(orchestration): cover tool safety gate`

## Code Style and Quality

- Follow existing style in the touched module.
- Prefer small, targeted changes.
- Add or update tests for behavior changes.
- Avoid introducing new heavy dependencies unless needed.

Run checks before opening a PR:

```bash
python Backend/manage.py check
python Backend/manage.py test
flake8 .
bandit -r . -x ./tests,./venv --skip B101
```

Note: some SQLite test environments may fail on JSON SQL functions. PostgreSQL-backed runs are preferred for full validation.

## Pull Request Checklist

- [ ] Problem and solution are clearly described
- [ ] Tests added/updated for changed behavior
- [ ] Security implications considered (auth, access control, data handling)
- [ ] Docs updated (`README.md`, `AGENTS.md`, or `docs/*`) if needed
- [ ] No unrelated file churn

## Review Expectations

- Maintainers may request scope reduction for large PRs.
- High-risk areas (payments, auth, orchestration, security) need stronger test coverage.
- Breaking changes must include migration and rollback notes.

## Reporting Security Issues

Do not open public issues for vulnerabilities.
Use the process in `SECURITY.md`.

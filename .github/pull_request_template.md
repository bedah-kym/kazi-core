## Summary

<!-- What changed and why? -->

## Type of change

- [ ] Bug fix
- [ ] Feature
- [ ] Refactor
- [ ] Documentation
- [ ] Test-only

## Scope

<!-- List key modules/files touched -->

## Validation

- [ ] `python Backend/manage.py check`
- [ ] `python Backend/manage.py test` (or targeted test run)
- [ ] Manual verification completed (if UI or integration change)
- [ ] Each new test shown failing without the change (paste the failure below)
- [ ] Touched test modules run alone and in the full suite
- [ ] CI's exact lint and Bandit commands run (`flake8` E/W/F; `bandit -r Backend --skip B101,B110`)
- [ ] No assertion removed or loosened, and no pinned number raised, without the reason and measurement below

Never be lazy in testing, tokens be damned: see
[`docs/golden-principles/tests-and-evals.md`](../docs/golden-principles/tests-and-evals.md).

Validation details:

```text
# Paste key command output or short summary
```

## Risk and rollback

<!-- Any migration, data, or operational risk? -->
<!-- How to roll back safely? -->

## Checklist

- [ ] Tests added/updated for changed behavior
- [ ] Docs updated (`README.md`, `AGENTS.md`, or `docs/*`) if needed
- [ ] Security considerations reviewed (auth, permissions, input sanitization)
- [ ] No unrelated changes included

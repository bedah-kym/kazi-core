# Backend Test Guide

This directory contains public, shareable test code for Kazi Core.

## What belongs here

- Deterministic unit tests
- Mocked integration tests
- Regression tests for bugs fixed in public code
- Scenario tests that do not require real credentials

## What does not belong here

- Real API keys, tokens, session cookies, or webhook secrets
- Personal email addresses, phone numbers, or customer data
- One-off debug scripts tied to a single machine
- Tests that only pass against a private frontend or private branch

## Safe defaults

- Use fake values such as `example@example.com`, `test-user`, or `fake-token`
- Mock external APIs instead of calling real services
- Prefer Django `SimpleTestCase` or isolated fixtures where possible
- Keep test inputs small and readable

## Local-only scripts

If you want to write a personal verification helper, use one of these names:

- `Backend/tests/local_<name>.py`
- `Backend/tests/manual_<name>.py`

These patterns are ignored by git on purpose.

Use local-only scripts for:

- trying a connector against your own credentials
- checking a dev server manually
- reproducing a bug with environment-specific setup

## Before committing a test

- Remove secrets and machine-specific values
- Replace live HTTP calls with mocks unless the file is explicitly an example
- Make sure the file name starts with `test_` only if it is meant to run in CI
- Add a short docstring explaining the behavior being covered

## Agentic tests in this repo

- `test_agentic.py` covers unit-level agentic behavior
- `test_agentic_scenarios.py` covers end-to-end mocked scenarios

If you add more agentic tests, prefer extending those files before creating many new ad hoc scripts.

# Golden principles: tests and evals

> **Never be lazy in testing, tokens be damned.**
> Tests are the last line of defense against bad code. Writing the extra test, running the extra
> lane and proving the test can fail always cost less than a bug in an agent that runs commands on
> someone's machine. This applies to every author: people, Claude, DeepSeek, any agent.

Prefer tasks that are hard to solve and easy to verify. Every change ships its verifier.

## A test must be able to fail
- DO write the failing test first for a bug fix. Show it failing, then passing.
- DO show every new test failing without the change: against `main` for a bug fix, against the
  previous commit for a review fix. Paste that failure in the PR. A test that passes either way proves
  nothing. (#303: a history test never sent the message it claimed to exclude.)
- DO call the production code. A test that re-implements the logic it checks can never fail when that
  logic breaks. (#303: a claim-race test wrote the database update by hand.)
- DO assert the one correct outcome. Accepting any of several end states hides the bug. (#303: a
  shutdown test accepted `completed`, so it passed without the fix.)
- DO assert what the user sees, not only the state: the saved notice, its exact text, the frame that
  reaches the room. (#303: a notice told users "some steps may have run" for messages that never ran.)

## Never weaken a test to make it pass
- DON'T delete or loosen an assertion without saying why in the PR. (#303: the lite test stopped
  checking the "reply saved" frame, which is the frame behind the old empty-bubble bug.)
- DON'T raise a pinned number (query count, token count, timing) without the measured list behind it
  in the PR, and remove what is duplicated first. (#303: a query pin went from 9 to 20 with no reason.)
- DON'T change an existing test's expectation unless the contract really changed. Say what changed.

## Test at the real layer
- DO mock below the boundary you are testing, never above it. Fake the model at the LLM client; use
  the real consumer, runner, database rows and channel layer. (#298: three provider bugs passed tests
  that mocked above the layer where they lived.)
- DO mock the LLM (`get_llm_client()`). Tests never touch the network or a real provider.
- DO build provider tests from recorded real responses: cut-offs, broken arguments, refusals, errors.
- Provider adapters: one multi-step tool-call fixture per provider. Check that reasoning and tool
  results stay coherent inside a turn and reset on a new user message.

## Concurrency, timing and bounds
- DO make racers really race: synchronise them with a barrier or event at the production seam, so both
  have read before either writes. Two tasks on one executor cannot overlap and prove nothing. (#303.)
- DO wait on the condition that matters (the paused row leaves `paused`), never a fixed sleep or a
  condition that is already true. Give every wait a timeout so a regression fails instead of hanging.
- DO prove a time bound by stalling the dependency (a database call that never returns) and asserting
  the bound still holds.
- DO pin time and timezone in reminder and schedule tests.

## Isolation
- DO reset module-level state in `setUp` with `addCleanup`: circuit breakers, caches, registries,
  singletons. (#303: one test left a tool's circuit breaker open and broke a later module, but only in
  CI's order.)
- DO run every touched test module alone and in the full suite. Order-dependent failures hide in both.

## Failure paths and honesty
- DO test failure paths: provider timeout, Redis eviction, approval expired, circuit breaker open,
  a write that fails, a shutdown mid-step.
- DO test that what Kazi says matches what happened. The worst bugs in this repo were Kazi saying one
  thing while the system did another.
- DON'T assert on LLM prose. Assert on structure: chosen tool, parameters, status, receipt, saved text
  that the harness wrote.

## Before calling it done
- DO run CI's exact commands, not a narrower version (for example `bandit -r Backend --skip B101,B110`).
  (#303: a narrower Bandit run passed and CI failed.)
- DO run a live check for sensitive or core changes, driven like a real user, plus a stress test past
  the limits. Tests are the last line of defense, not the only one: live runs and outside review found
  bugs every test missed.
- DO give each new capability a scenario: a goal, a fixture, and a verifier script that exits 0 or 1.
  No verifier, no merge.
- DON'T call work done on "should work". Paste the command and the output tail.

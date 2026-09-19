# Golden principles: tests and evals
Prefer tasks that are hard to solve and easy to verify. Every change ships its verifier.

- DO write the failing test first for a bug fix. Show it failing, then passing.
- DO give each new capability a scenario: a goal, a fixture, and a verifier script that exits 0 or 1. No verifier, no merge.
- DO mock the LLM (`get_llm_client()`). Tests never touch the network or a real provider.
- DO pin time and timezone in reminder and schedule tests.
- DO test failure paths: provider timeout, Redis eviction, approval expired, circuit breaker open.
- DON'T assert on LLM prose. Assert on structure: chosen tool, parameters, status, receipt.
- DON'T call work done on "should work". Paste the command and the output tail.
- Provider adapters: one multi-step tool-call fixture per provider (DeepSeek, Claude). Check that reasoning and tool results stay coherent inside a turn and reset on a new user message.

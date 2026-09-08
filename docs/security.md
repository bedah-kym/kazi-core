# Security & Trust

Security isn't a feature we bolted on after the fact — it sits *inside* the
loop, between the agent and every tool it calls. Here's what stands between a
prompt and a mistake.

## The layers

### Prompt-injection detection
Regex-based checks run before anything reaches the LLM, catching common
injection attempts with zero added latency. It won't catch every novel attack —
nothing will — but it stops the scripted ones cold. A golden injection corpus
(`orchestration/eval/golden_scenarios.json`) runs in the eval harness so a
regression in the detector fails CI.

### Parameter sanitization
Every tool call is scrubbed of restricted keys — `api_key`, `token`,
`password`, and friends — before it goes out. The model can ask for a token;
it can't accidentally get one back.

### Risk-level gates
Actions are graded by risk. Low-risk actions run immediately. High-risk
actions — sending email, moving money, booking travel — pause for confirmation
first.

### Room-scoped access
Users can only act inside their own chatrooms. Cross-room access is resolved
through explicit linking, not by default. The access check is cached but
**fails closed** — a membership change invalidates the cache, and a missing or
unresolvable lookup denies access rather than allowing it.

### Agent budget caps
The agent loop and its sub-agents run under hard budget caps (iterations, tool
calls, token spend). An install-level **"Enforce agent budget caps"** toggle in
settings (default on) gates these; when it's off, a hard backstop still prevents
an unbounded loop. Sub-agents get tighter budgets than the parent loop and can
never pause for confirmation.

### Encryption at rest
Optional AES-256-GCM encryption for per-room messages. Turn it on where the
data warrants it.

### Action receipts
Sensitive actions leave a sanitized audit record: what ran, with which
parameters, and whether it's reversible. You can review what the agent did and
undo what it shouldn't have. Receipts are **append-only** — once written, an
undo cannot resurrect earlier state.

### Output guardrails
Secrets and PII are redacted from tool results and replies before they reach
the user — or re-enter the model. Tool errors are clamped and flattened so raw
upstream bodies never leak into the LLM's context.

### Context budgets
Room context and conversation history are capped, and when they truncate, the
system emits an observable event instead of silently overflowing the window.

### Connector registry guardrails
Pip-installed (entry-point) connectors **cannot shadow** an action name owned by
a built-in connector — the conflicting registration is refused and logged. Any
catalog entry that violates the tool-schema contract is recorded and surfaced,
so a malformed connector can't silently redefine the tool surface.

## Where it lives

| Concern | File |
|---|---|
| Security policy | `Backend/orchestration/security_policy.py` |
| Risk gates & error handling | `Backend/orchestration/tool_executor.py` |
| Audit receipts | `Backend/orchestration/action_receipts.py` |
| Agent budget caps | `Backend/orchestration/agent_loop.py`, `user_preferences.py` |
| Connector guardrails | `Backend/orchestration/connector_registry.py` |

See the [contracts](contracts/README.md) for the exact shapes connectors must
respect.

## Being honest with you

Kazi is **early access**. Breaking changes are possible before v1.0. If you
find a vulnerability, don't file a public issue — follow the
[security policy](https://github.com/bedah-kym/kazi-core/blob/main/SECURITY.md)
so we can fix it quietly first.

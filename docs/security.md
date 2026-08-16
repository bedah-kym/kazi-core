# Security & Trust

Security isn't a feature we bolted on after the fact — it sits *inside* the
loop, between the agent and every tool it calls. Here's what stands between a
prompt and a mistake.

## The layers

### Prompt-injection detection
Regex-based checks run before anything reaches the LLM, catching common
injection attempts with zero added latency. It won't catch every novel attack —
nothing will — but it stops the scripted ones cold.

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
through explicit linking, not by default.

### Encryption at rest
Optional AES-256-GCM encryption for per-room messages. Turn it on where the
data warrants it.

### Action receipts
Sensitive actions leave a sanitized audit record: what ran, with which
parameters, and whether it's reversible. You can review what the agent did and
undo what it shouldn't have.

### Output guardrails
Secrets and PII are redacted from tool results and replies before they reach
the user — or re-enter the model. Tool errors are clamped and flattened so raw
upstream bodies never leak into the LLM's context.

### Context budgets
Room context and conversation history are capped, and when they truncate, the
system emits an observable event instead of silently overflowing the window.

## Where it lives

| Concern | File |
|---|---|
| Security policy | `Backend/orchestration/security_policy.py` |
| Risk gates & error handling | `Backend/orchestration/tool_executor.py` |
| Audit receipts | `Backend/orchestration/action_receipts.py` |

See the [contracts](contracts/README.md) for the exact shapes connectors must
respect.

## Being honest with you

Kazi is **early access**. Breaking changes are possible before v1.0. If you
find a vulnerability, don't file a public issue — follow the
[security policy](https://github.com/bedah-kym/kazi-core/blob/main/SECURITY.md)
so we can fix it quietly first.

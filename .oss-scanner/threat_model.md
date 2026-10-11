# Threat model

## What this project does and where untrusted input enters
Kazi Core is a self-hostable Django + Channels backend that runs an LLM agent loop (ReAct), plans multi-step
workflows, and executes tool calls through a connector registry, including a governed shell (`run_command`).
It is multi-user: one deployment serves many users, each with private rooms, workflows, credentials and wallets.

Treat all of the following as attacker-controlled:
- HTTP requests to every route mounted in `Backend/Backend/urls.py` (`api/`, `chatbot/`, `accounts/`, `workflows/`,
  `api/workflows/`, `payments/`, `notifications/`, `travel/`, `uploads/`, `auth/`), from anonymous and
  authenticated users alike. An authenticated user is an attacker against every other user.
- WebSocket frames on `ws/chat/<room>/` (`Backend/chatbot/consumers.py`) and `ws/notifications/`.
- Webhooks and OAuth callbacks: IntaSend (`payments/views.py`), Calendly (`Api/views.py`,
  `workflows/webhook_handlers.py`, `orchestration/webhook_validator.py`), Gmail/Calendly OAuth callbacks.
- Uploaded files (documents, voice) and their contents.
- Everything the LLM produces, and everything a tool returns to it: web pages, emails, shell output, connector
  responses, sub-agent output. Per `docs/golden-principles/core-invariants.md`, untrusted text is data, never
  instructions. A prompt injection that makes the agent take an action the user did not approve is in scope.

## Security goals (what must hold)
- **Tenant isolation:** no user can read or act on another user's rooms, messages, workflows, approvals, grants,
  receipts, credentials or wallet. Room access goes through `orchestration/security_policy.py`
  (`user_has_room_access`) and per-query `user=` scoping.
- **Human gates:** a risky tool call (see `orchestration/tool_executor.py` risk gates and `action_catalog.py`
  risk levels) runs only after a valid, unexpired, correctly scoped approval or standing grant
  (`workflows/runtime.py`, `workflows/grants.py`). Anything that skips, forges, replays or widens an approval is a
  bypass.
- **Taint:** once a run has seen untrusted tool output (`_UNTRUSTED_TOOL_NAMES` in
  `orchestration/agent_loop.py`), external-write and credential actions must move up an approval tier.
- **Governed shell:** `run_command` executes only through the sidecar (`orchestration/shell_exec/daemon.py`,
  token-authenticated) under a profile from `orchestration/shell/profiles.py`. The `standard` and `locked`
  profiles (Docker, unprivileged, read-only rootfs, egress via allowlisted proxy) are the sandbox boundary: escaping
  them, reaching Kazi's database, Redis, provider keys or secrets from inside them, or bypassing the egress
  allowlist (`orchestration/shell/egress.py`) is in scope. The command classifier (`shell/classifier.py`) only
  reduces prompts and is not itself a boundary, but a misclassification that removes a required approval is in scope.
- **Fail closed:** if a capability lookup, room-access check, cache or approval store errors, the action must be
  denied.
- **Receipts are append-only** (`orchestration/action_receipts.py`) and must record every sensitive action.
- **Secrets** (API keys, OAuth tokens, `ENCRYPTION_KEY`, per-room keys in `users/encryption.py` /
  `chatbot/models.py`) never reach prompts, logs, receipts, the sandbox, or another user.
- **Money** (`payments/`): double-entry ledger, Decimal cent quantization, balanced journals, idempotent webhook
  handling. Crediting a wallet without a verified payment, double-crediting, or moving another user's funds is
  critical.

## Components that matter most / least
- Most: `Backend/orchestration/` (agent loop, tool executor, tool router, security policy, shell, shell_exec,
  coordinator, action receipts), `Backend/workflows/` (approvals, grants, webhook handlers), `Backend/chatbot/`
  (consumers, uploads, room APIs), `Backend/payments/`, `Backend/users/` (auth, encryption).
- Medium: individual connectors in `Backend/orchestration/connectors/`, `Backend/notifications/`, `Backend/Api/`.
- Least: `Backend/travel/` (showcase domain, still a live app), `examples/` (only loaded with `KAZI_DEMO_MODE`).
- Out of scope: `docs/`, `e2e/`, `scripts/`, test files (`tests.py`, `test_*.py`, `Backend/tests/`), and the
  `open` shell profile's host access (it runs on the host by design; it is opt-in and documented as unsandboxed).
- Deployment misconfiguration (e.g. `DEBUG=true`, weak `DJANGO_SECRET_KEY`) is out of scope unless a default
  shipped in this repository is insecure.

## How to exercise it
- The image is built like CI's hermetic lane: SQLite, LocMem cache, in-memory broker, no Postgres or Redis.
- Full suite: `python Backend/manage.py test --noinput` (about 3 minutes). Targeted:
  `python Backend/manage.py test --noinput orchestration.test_shell_daemon`.
- Prompt-injection / planner corpus: `python Backend/manage.py run_golden_eval` (no LLM needed).
- Django test client and Channels `WebsocketCommunicator` are the easiest way to drive views and consumers.
  The shell sidecar app accepts an injected backend (`create_app` in `orchestration/shell_exec/daemon.py`).
- Docker is not available inside the image, so sandbox-escape claims against `DockerBackend` must be argued from
  the code and the container options it passes.
- Design notes: `docs/security.md`, `docs/contracts/` (approval, credential scoping, replay safety),
  `docs/kazi-shell-exec-design.md`, `docs/golden-principles/core-invariants.md`.

## How you rate severity
- Critical: unauthenticated RCE; sandbox escape from `standard`/`locked` to the host or to Kazi's secrets/DB;
  cross-tenant takeover or reading another user's credentials; unauthorized wallet credit or fund movement.
- High: approval/grant/human-gate bypass (including via prompt injection) leading to an external write, a message
  sent, money moved or a shell command run; authenticated cross-tenant data read; webhook signature bypass;
  secret leakage into prompts, logs, receipts or the sandbox; egress allowlist bypass.
- Medium: prompt injection that changes agent behaviour without crossing an approval gate; receipt tampering or a
  missing receipt; stored XSS in rendered content; fail-open on an error path without a demonstrated bypass.
- Low: DoS (including resource exhaustion and LLM-budget burn), information disclosure of non-sensitive metadata,
  hardening suggestions.
- A finding needs a concrete path from an attacker-controlled input to the impact; please include a test using the
  Django test client or the existing test helpers as the reproducer.

## Anything to leave alone
- Regex prompt-injection detection (`_INJECTION_PATTERNS` in `security_policy.py`) is defence in depth, not a
  boundary; "this phrasing is not detected" alone is not a finding unless it leads to a gate bypass.
- Do not report the `open` shell profile running commands on the host; that is its documented purpose.
- Do not report dependencies' CVEs without a reachable path in this code.
- Patches should be minimal, keep cent quantization (no floats), and never update or delete receipts.

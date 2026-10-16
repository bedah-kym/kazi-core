# Features

A tour of what Kazi Core ships out of the box, and where to find each feature.

## The headline features

### 🔁 One agent loop for every chat turn

Every message that reaches Kazi runs through one path: the ReAct agent loop
(`orchestration/agent_loop.py`) — the model thinks, calls a tool, observes the
result, and repeats. The coordinator no longer plans, parses intent, or routes
to a second pipeline; the planner and intent router were removed. A multi-step
request the agent composes is handed to `execute_adhoc_workflow`, which starts
and runs it. *Read more:* [Architecture](architecture.md).

### 🖥️ A governed shell: `run_command`

Kazi's reach beyond connectors is one tool, `run_command`, executed by a
deliberately dumb sidecar (`python Backend/manage.py run_shell_exec`) inside a
sandboxed container: non-root, read-only root filesystem, `--cap-drop=ALL`,
network off by default, with memory and PID caps. The sandbox is the security
boundary; the command classifier is UX and a tripwire.

Isolation profiles (`open`, `standard`, `locked`) are resolved per room, else
from the global default. `standard` (the default) and `locked` run in Docker;
`open` runs unsandboxed on the host and is meant for disposable boxes only.
A per-room workspace persists across commands, and a destructive command is
snapshotted first so it can be rolled back. *Read more:*
[Security](security.md), [Configuration](configuration.md).

### ⏸️ Durable human approvals

High-risk actions — sending email, moving money, booking travel — pause for a
human "yes" before they run. Inside the agent loop the paused call is stored as
a room-scoped `WorkflowApprovalRecord` (`kind=agent_loop`) that survives Redis
eviction, so it can be resumed later. An approval reply must be the whole
message: `ok, but use a different folder` is not consent — it cancels the
pending action and says so. *Read more:* [Operate a Workflow](operate-a-workflow.md).

### 🧾 Every sensitive action leaves a receipt

Sensitive actions write an append-only `ActionReceipt`: the action, its
parameters (sanitized), the result, the status, and whether it is reversible.
Receipts are never overwritten, so the audit trail is not resurrected by an
undo. A shell receipt also records why each command ran (`approval_basis`).
*Read more:* [Security](security.md).

### 🛡️ Untrusted text is data, never instructions

Output from the shell, a web search or a delegated run is untrusted. When such
output enters a room, the room is *tainted* for a window
(`AGENT_TAINT_TTL_SECONDS`, default 900s). While tainted, external-write and
credential-scoped actions move up one approval tier, stored "auto" rules ask
again, and egress shell commands ask again — a stored rule can never skip the
ask. A turn whose replayed history already contains untrusted output starts
tainted; sub-agents inherit their parent's taint and acquire their own. A cache
read failure counts as tainted. *Read more:* [Security](security.md).

### 🎓 Skills and routines (v0.7)

A skill is a folder with a `SKILL.md` that teaches the agent one task; only
skills in the `active` stage are exposed, and the agent can discover and load
them mid-conversation with the `list_skills` / `load_skill` meta-tools. The
`save_skill` meta-tool writes a *staged* skill that a human promotes later.

A routine binds a skill to a schedule or event. A schedule/webhook definition
must answer the routine contract (owner, inputs, output, approval boundary,
no-data policy, partial completion, idempotency) before it can be enabled, and
it cannot run under a standing grant until a test run passes. After a long
absence Kazi asks once whether routines should keep running; no answer pauses
them. *Read more:* [Skills](skills.md), [Add a Workflow](add-a-workflow.md).

### 🤝 Standing grants (v0.7)

A standing grant is "permission once" done per rule: scoped to a workflow
version, a trigger and a capability — never a global unlock. A grant is created
only from a human decision on an approval card (`allow once` / `always allow` /
`deny`), and "ask first" wins when rules overlap. It lapses on version drift, a
failure spike, an out-of-scope action, or after `STANDING_GRANT_LIFETIME_DAYS`
(default 30). *Read more:* [Configuration](configuration.md).

### 👥 Personas and specialist handoffs (v0.7)

Personas are named specialists with a deterministic tool scope and a risk
ceiling, managed from a dashboard. The internal handoff runtime — one owner per
stage, a budget cap, and an access re-check at run time — ships too; a way to
start a handoff from chat comes later. *Read more:* [Architecture](architecture.md).

### ⏱️ Workflows with schedules and a human-gated runtime

A workflow is a versioned JSON definition that sequences connector actions and
marks which ones need approval. Triggers are webhook, schedule or manual; a
schedule is registered with the Temporal scheduler. A scheduled workflow asked
for in chat is always confirmed before it is created, whatever the rules or
taint say. Operators approve, reject, cancel, rerun and replay through the
`/workflows/` console and the JSON API. A live definition is never edited in
place — every change appends a new version. *Read more:*
[Operate a Workflow](operate-a-workflow.md), [Add a Workflow](add-a-workflow.md).

### 🧪 The golden evaluation

`python Backend/manage.py run_golden_eval` runs a corpus of golden scenarios
through the real agent loop with the model scripted and tools stubbed, so the
gate behaviour (auto-run, pause, refuse) is checked with no API key and no
network. Scenarios are grouped into capability packs, and `test_scenario_packs`
fails CI when a pack falls below `SCENARIO_PACK_MIN_SIZE`. The injection corpus
runs here too, so a regression in the detector fails the eval. *Read more:*
[Evaluation](eval.md).

### 🧠 An agent that remembers

Three tiers of memory persist across conversations:

- **Hot** — the current room summary, active topics, and recent decisions (always in context)
- **Warm** — extracted entities, action items, and insights with confidence scores
- **Cold** — daily and weekly compressed summaries for long-term recall

The agent also learns your preferences — tone, date format, currency, verbosity,
and more — and adapts over time.

*Tuning:* see the memory and context budget settings in [Configuration](configuration.md).

### 🔗 Connectors: your code is the extension point

Everything external goes through a connector. A connector is **one file** that
declares its actions and implements an `execute()` method; the runtime
auto-discovers it on restart. *Build your own:*
[Add a Connector](add-a-connector.md).

## What else ships

| Feature | What it does |
|---|---|
| Multi-step workflows | Composed and executed as one run, with approval gates |
| Multi-provider LLM | Anthropic (Claude), DeepSeek (full tool-calling), and Hugging Face, with automatic fallback |
| Rooms that link | Context flows between chat rooms so a decision in one is visible in another |
| Contacts | A first-class contact system the agent can read, write, and deduplicate |
| Telegram messaging | Send text, media, and inline keyboards via the Telegram Bot API |
| Voice messages | Record voice notes, auto-transcribed for the agent |
| Document uploads | PDFs and images, with text/metadata extraction |
| Threaded replies | Parent–child threading for focused sub-conversations |
| Double-entry ledger | ACID financial accounting — real debits and credits |
| Recurring invoices | Monthly/quarterly/yearly billing cycles with dispute tracking |
| Reminders | *"Remind me to call John in 10 minutes"* — in-app, email, or WhatsApp |
| Timezone-aware reminders | LLM time parser with clarification support, scheduled in your timezone |
| Quota system | Transparent per-user rate limits with color-coded status |
| Content moderation | Batched moderation with auto-muting after a threshold |
| Telemetry | JSONL event log for every loop, tool call, and memory update |
| Payments | Wallet, invoices, and transactions with M-Pesa and card support via IntaSend |
| Travel | Flight, hotel, bus, transfer, and event search via Amadeus |
| Retry backoff + circuit breaker | Exponential backoff and per-service degrade for flapping integrations |
| Dependency cycle detection | Kahn's-algorithm check that rejects circular workflow definitions |
| History compaction | Trims oldest turns to the context budget — on by default |
| Operator web UI | Workflow operations inbox, run history, and approval forms at `/workflows/` |
| Notification center | Full inbox at `/notifications/` with unread/event filters and deep links |
| Model selector | Per-room model picker backed by the frozen model catalog |

## Turning features on

Most features are opt-in through environment variables (API keys, budget caps,
and flags). See [Configuration](configuration.md) for the full reference, or
[Deploy Safely](deploy-safely.md) for the production checklist.

Two pieces are optional in a local setup. **Redis and Postgres** have a
debug-only in-memory fallback (in-memory channel layer, LocMem cache, SQLite,
inline Celery) — but inline means no worker and no scheduler, so a reminder set
for a future time is stored but not delivered. **Temporal** is optional
(`TEMPORAL_DISABLED`); without it, workflows that need durable approvals are
queued rather than run. [Configuration](configuration.md) states both in full.

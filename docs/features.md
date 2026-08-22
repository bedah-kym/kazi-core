# Features

A tour of what Kazi Core ships out of the box, and where to find each feature.

## The headline features

### 🧠 An agent that remembers

Three tiers of memory persist across conversations:

- **Hot** — the current room summary, active topics, and recent decisions (always in context)
- **Warm** — extracted entities, action items, and insights with confidence scores
- **Cold** — daily and weekly compressed summaries for long-term recall

The agent also learns your preferences — tone, date format, currency, verbosity,
and more — and adapts over time.

*Tuning:* see the memory and context budget settings in [Configuration](configuration.md).

### 💬 Rooms that talk to each other

Room linking lets context flow between chat rooms. A decision logged in one
room is visible to the agent in another; contacts, high-priority notes, and
references resolve across linked rooms automatically.

### 📇 Contacts live in the agent

A first-class contact system the agent can read, write, and deduplicate.
Contacts are extracted from conversations automatically, so *"send John the
invoice"* resolves the email without copy-pasting.

### 🧾 Every action has a receipt

Sensitive actions (email, money, travel) generate audit receipts — sanitized
records of what happened, with which parameters, and whether it's reversible.
Review what the agent did and undo what it shouldn't have.

### ⏸️ Durable human checkpoints

Workflows pause durably for approval instead of relying on ephemeral chat
state. A run can wait on Temporal, notify an operator over in-app, email, or
WhatsApp, and resume only after an explicit decision. High-risk tools paused
inside the agent loop get the same durable treatment — a room-scoped approval
record that survives Redis eviction and shows up in the ops inbox.

*Driving a run:* [Operate a Workflow](operate-a-workflow.md).

### 🧭 One routing brain

`OrchestrationCoordinator` owns everything after `@mathia` routing — directives,
pending confirmations, the agent loop, planner, intent dispatch, and general
chat. The WebSocket consumer is thin: it validates, encrypts, persists, and
hands off.

### 🔁 Retries that back off, services that trip

Failed tool calls retry with exponential backoff (capped at 300s), and a
service that fails repeatedly trips a per-service circuit breaker (60s
cooldown) so a flapping integration degrades instead of being hammered.

### 🧮 Plans can't loop

Workflow step dependencies are checked for cycles (Kahn's algorithm), so an
`A→B→A` plan is rejected with a clear error instead of silently reordering
wrong.

### 🛡️ Security in the bones

- Prompt-injection detection before anything reaches the LLM
- Parameter sanitization (strips tokens, keys, passwords)
- Risk-level gates — low risk runs immediately, high risk pauses
- Room-scoped access control
- Optional AES-256-GCM per-room encryption at rest
- Output guardrails that redact secrets and PII
- Context budgets with observable truncation instead of silent overflow

### 🔔 Notifications everywhere

One pipeline routes events to in-app (real-time WebSocket), email, or WhatsApp
based on user preference — with debounce to prevent spam.

### 🔗 Connectors: your code is the extension point

Everything external goes through a connector. A connector is **one file** that
declares its actions and implements an `execute()` method; the runtime
auto-discovers it on restart.

*Build your own:* [Add a Connector](add-a-connector.md).

## What else ships

| Feature | What it does |
|---|---|
| Multi-step workflows | *"Book a flight, find a hotel, email my boss the itinerary"* — planned, verified, executed as one run |
| Manager verifier | A deterministic supervisor that reorders steps, fills missing params, and catches bad plans |
| Multi-provider LLM | Anthropic (Claude), DeepSeek (full tool-calling), and Hugging Face, with automatic fallback |
| Skills | Drop-in `SKILL.md` instruction packs the agent discovers and loads on demand |
| Telegram messaging | Send text, media, and inline keyboards via the Telegram Bot API |
| Voice messages | Record voice notes, auto-transcribed for the agent |
| Document uploads | PDFs and images, with text/metadata extraction |
| Threaded replies | Parent–child threading for focused sub-conversations |
| Double-entry ledger | ACID financial accounting — real debits and credits |
| Recurring invoices | Monthly/quarterly/yearly billing cycles with dispute tracking |
| Reminders | *"Remind me to call John in 10 minutes"* — in-app, email, or WhatsApp |
| Quota system | Transparent per-user rate limits with color-coded status |
| Content moderation | Batched moderation with auto-muting after a threshold |
| Telemetry | JSONL event log for every loop, tool call, and memory update |
| Payments | Wallet, invoices, and transactions with M-Pesa and card support via IntaSend |
| Travel | Flight, hotel, bus, transfer, and event search via Amadeus |
| Orchestration coordinator | One routing facade that owns post-`@mathia` decisions |
| Retry backoff + circuit breaker | Exponential backoff and per-service degrade for flapping integrations |
| Dependency cycle detection | Kahn's-algorithm check that rejects circular workflow plans |
| History compaction | Trims oldest turns to the context budget — on by default |

## Turning features on

Most features are opt-in through environment variables (API keys, budget caps,
and flags). See [Configuration](configuration.md) for the full reference, or
[Deploy Safely](deploy-safely.md) for the production checklist.

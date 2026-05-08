<div align="center">
  <img src="assets/kazi-core.png" alt="Kazi Core Engine Mascot" width="200"/> 
  <h1>Kazi Core</h1>
  
  <p>
    <img src="https://img.shields.io/badge/python-3.11%20%7C%203.12-blue" alt="Python" />
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License" />
    <img src="https://github.com/bedah-kym/kazi-core/actions/workflows/main.yml/badge.svg" alt="CI" />
    <a href="https://codecov.io/gh/bedah-kym/kazi-core"><img src="https://codecov.io/gh/bedah-kym/kazi-core/branch/main/graph/badge.svg" alt="codecov" /></a>
    <img src="https://img.shields.io/badge/status-early%20access-orange" alt="Status" />
  </p>

  <p><strong>A self-hostable runtime for human-supervised agent workflows.</strong></p>
</div>

**Kazi Core** is built for teams who want AI agents that call their own tools, run on their own infrastructure, and keep their data in their own stack. **No vendor lock-in. No data leaving your server.**

> 💡 **Kazi** is Swahili for *work*. The engine does the heavy lifting for you.

---

## ❓ Why Kazi?

Most agent frameworks give you a loop and a prompt.
Kazi gives you the loop, the memory, the security, the approval gates,
the durable workflow runtime, and the connector architecture — all wired together,
all self-hosted, all yours.

Here's what ships out of the box:

### 🧠 Your agent remembers

Kazi has a **3-tier memory system** that actually persists across conversations:

- **Hot** — room summary, active topics, recent decisions (always in context)
- **Warm** — extracted entities, action items, insights with confidence scores
- **Cold** — daily and weekly compressed summaries for long-term recall

The agent doesn't just respond — it *learns*. It tracks preferences like your preferred
tone (formal? direct? friendly?), date format (DD/MM or MM/DD), currency, verbosity,
and 12+ other dimensions. Over time, it adapts to how *you* communicate.

### 💬 Your rooms talk to each other

**Room linking** lets you connect chat rooms so context flows between them.
A decision logged in your "Q1 Planning" room is visible to the agent in your
"Client Outreach" room. Contacts, high-priority notes, and references
resolve across linked rooms automatically.

Say *"email the client from that other thread"* — Kazi knows who you mean.

### 📇 Your contacts live in the agent

Kazi has a **first-class contact system** the agent can read, write, and deduplicate.
Contacts are extracted from conversations automatically. When you say *"send John the invoice"*,
the agent looks up John's email, resolves ambiguity, and fills in the blanks — no copy-pasting.

### 🧾 Every action has a receipt

Sensitive actions (sending emails, moving money, booking travel) generate
**audit receipts** — sanitized logs of what happened, what parameters were used,
and whether the action is reversible. Users can review what the agent did
and undo what it shouldn't have.

### ⏸️ Durable human checkpoints

Kazi workflows can now pause durably for human approval instead of relying on
ephemeral chat state. A workflow can wait on Temporal, notify an operator over
in-app, email, or WhatsApp, and resume only after an explicit decision lands.

### 🛡️ Security is in the bones

- **Prompt injection detection** — regex-based, zero-latency, catches common attacks before they reach the LLM
- **Parameter sanitization** — strips api_key, token, password, and other restricted fields from every tool call
- **Risk-level gates** — low-risk actions execute immediately, high-risk actions pause for confirmation
- **Room-scoped access control** — users can only act within their own chatrooms
- **AES-256-GCM encryption** — optional per-room message encryption at rest

### 🔔 Notifications across every channel

One unified system routes events to **in-app** (real-time WebSocket push),
**email**, or **WhatsApp** — based on user preferences. Payments, reminders,
system alerts, and messages all flow through the same pipeline.
Built-in debounce prevents notification spam.

---

## 📦 What Else Ships

| Feature | What it does |
|---------|-------------|
| **Multi-step workflows** | *"Book a flight, find a hotel, and email my boss the itinerary"* — planned, verified, and executed as one workflow |
| **Durable execution** | Workflows run on Temporal — they survive server restarts, retries, network failures, and approval waits |
| **Human checkpoints** | High-risk or approval-gated steps pause durably, emit approval records, and resume only after an operator decision |
| **Manager verifier** | A deterministic supervisor that reorders steps, fills missing params, and catches bad plans before execution |
| **Voice messages** | Record voice notes, auto-transcribed to text for the agent to process |
| **Document uploads** | Upload PDFs and images — Kazi extracts text and metadata via OCR/vision |
| **Threaded replies** | Messages support parent-child threading for focused sub-conversations |
| **Double-entry ledger** | ACID-compliant financial accounting — real debits and credits, not toy wallet balances |
| **Recurring invoices** | Set up monthly/quarterly/yearly billing cycles with dispute tracking |
| **Reminders** | *"Remind me to call John in 10 minutes"* — delivered via in-app, email, or WhatsApp |
| **Quota system** | Transparent per-user rate limits with color-coded status (searches, AI actions, uploads) |
| **Content moderation** | Batched message moderation with auto-muting after threshold |
| **Telemetry** | JSONL event log for every agent loop, tool call, and memory update |

---

## ⚖️ Why Not LangChain, OpenAI Agents SDK, or the rest?

Those tools are built for you to use *their* ecosystem.
The APIs, the tooling, the billing, the data — it all flows back to a US company.
You are a user, not an owner.

**Kazi is built to be owned:**

- Run it on your own server. Your data doesn't leave.
- Plug in *your* payment rail — M-Pesa, Stripe, whatever your market uses.
- Write connectors for *your* APIs, your local services, your language.
- Security ships in the core — not bolted on after a breach.
- Community governed. No VC deciding the roadmap.

This matters more outside the US, where the big platforms aren't built for
your market, your currency, or your infrastructure.

---

## 🚀 Project Status

**v0.4.0 — the human-gated runtime cycle.**

Workflows now pause durably for human approval, operators get a
first-class API + Django admin surface (approve / reject / cancel /
rerun / pause / resume), the deferred-run watchdog dead-letters
runs that wait too long, and a canonical demo workflow pack runs the
full `request → workflow → approval → receipt → replay` loop with no
real API keys via `KAZI_DEMO_MODE=true` plus
[`bash scripts/demo.sh`](scripts/demo.sh).

The cycle also collapsed connector loading into one extension path,
landed five v1.0 stable runtime contracts under
[`docs/contracts/`](docs/contracts/), promoted the eval harness to a
documented surface, shipped the `kazi_trace` CLI for debugging any
execution, and reorganized docs around five developer flows.

Container releases are **cosign-signed** (keyless via Sigstore) and
ship **SBOM + SLSA provenance**. CI runs against real Postgres + Redis
on Python 3.11 and 3.12.

Still early access — breaking changes possible before v1.0. See
[`CHANGELOG.md`](CHANGELOG.md) for what's in each release.

---

## 🌊 How Requests Flow

```
1. User sends a message over WebSocket
2. Kazi assembles context — history, memory, preferences, linked room notes
3. Planner decides: single action, multi-step workflow, or clarification
4. Tool executor runs connector actions with safety gates
5. Manager verifier reviews the plan (reorders, fills gaps, catches errors)
6. Temporal keeps long-running runs durable, including approval waits
7. Results are streamed back in real time
8. Receipts are logged. Memory is updated. Preferences adjust.
```

## 🏗️ Architecture

```text
Client (HTTP/WebSocket)
  -> ChatConsumer (Django Channels)
    -> Context assembly (memory + preferences + linked rooms)
      -> Planner (single-turn or multi-step)
        -> Manager Verifier (reorder, validate, fill gaps)
          -> Tool Executor (risk gates + confirmation)
            -> Temporal Runtime (signals, approvals, replay-safe execution)
              -> Connector Registry (auto-discovery)
                -> Connectors (built-in + yours)
                  -> Receipts + Telemetry + Ops Inbox
```

---

## ⚡ Quick Start

1. Clone:

```bash
git clone https://github.com/bedah-kym/kazi-core.git
cd kazi-core
```

2. Create `.env` in repo root:

```bash
DJANGO_SECRET_KEY=change-me
DJANGO_DEBUG=true
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=http://localhost:8000
DATABASE_URL=postgres://kazi_user:kazi_password@db:5432/kazi_db
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0
ANTHROPIC_API_KEY=sk-ant-...
```

3. Start services and initialize DB:

```bash
docker compose up --build -d db redis web celery_worker celery_beat
docker compose exec web python Backend/manage.py migrate
docker compose exec web python Backend/manage.py createsuperuser
```

4. Open `http://localhost:8000`.

Full guide: [`docs/quickstart.md`](docs/quickstart.md)

---

## 🔌 Built-in Connectors

These connectors are examples and useful defaults. The core product direction is
the runtime itself: connector authorship, approval gates, observability, and
durable execution.

| Connector | Actions | Service |
|-----------|---------|---------|
| Weather | `get_weather` | OpenWeather |
| Currency | `convert_currency` | Exchange Rate API |
| Search | `search_info` | Web search |
| Gmail | `send_email` | Gmail API (OAuth) |
| WhatsApp | `send_message` | Twilio |
| Payments | `check_balance`, `list_transactions` | IntaSend |
| Invoices | `create_invoice`, `create_payment_link` | IntaSend |
| Calendar | `schedule_meeting`, `check_availability` | Calendly |
| Travel | flights, hotels, buses, transfers, events | Amadeus + Buupass |
| Reminders | `set_reminder` | Built-in (Celery) |
| Contacts | `lookup_contact`, `save_contact` | Built-in |
| Notes | `create_note`, `complete_note` | Built-in |

---

## 🧪 Demo Workflow Ideas

- **Scheduled digest**: run a search on a cron trigger, then pause before sending the summary email.
- **Approval-gated incident email**: gather context, draft the message, wait for approval, then send and log a receipt.
- **Failure and replay**: mark discovery steps as replay-safe so an operator can rerun from the failed step without replaying the side effect.

## 🛠️ Create a Connector

Drop a file in `Backend/orchestration/connectors/` — it auto-registers on restart:

```python
from orchestration.base_connector import BaseConnector


class MyConnector(BaseConnector):
    name = "my_service"
    version = "0.1.0"
    actions = ["do_something"]

    def get_action_catalog_entries(self):
        return [{
            "action": "do_something",
            "service": "my_service",
            "description": "Does something useful",
            "params": {
                "input": {
                    "type": "string",
                    "required": True,
                    "description": "Input value",
                }
            },
            "risk_level": "low",
        }]

    async def execute(self, parameters, context):
        return {"status": "success", "message": "Done", "data": {}}
```

Connectors also install via pip — register a `kazi.connectors` entry point
and it auto-discovers on startup.

Full guide: [`docs/writing-a-connector.md`](docs/writing-a-connector.md)

---

## 📂 Repository Layout

```text
Backend/
  orchestration/        # Agent core — start here
    agent_loop.py       # ReAct engine (think -> act -> observe)
    workflow_planner.py # Multi-step plan decomposition + execution
    manager_verifier.py # Deterministic plan verification + reordering
    tool_executor.py    # Tool dispatch + safety gates
    base_connector.py   # Connector interface
    connector_registry.py  # Auto-discovery (directory + entry points)
    action_catalog.py   # Tool definitions + risk levels
    security_policy.py  # Injection detection, sanitization
    action_receipts.py  # Audit trail for sensitive actions
    memory_state.py     # Entity tracking + preference learning
    contact_tools.py    # LLM-accessible contact system
    memory_tools.py     # Note creation + archival tools
    llm_client.py       # LLM provider abstraction
    connectors/         # Built-in connectors (add yours here)
  chatbot/              # WebSocket consumers, memory, context, contacts
  notifications/        # Unified in-app, email, WhatsApp notifications
  workflows/            # Durable workflow execution (Temporal)
  travel/               # Multi-modal travel search + booking
  payments/             # Double-entry ledger, invoices, wallets
  users/                # Auth, profiles, quotas, encryption
```

## 💻 Tech Stack

- Python 3.11, Django 5.x (ASGI)
- Django Channels + Redis (WebSocket + real-time)
- PostgreSQL
- Celery + Celery Beat (async tasks + scheduling)
- Temporal (optional — durable multi-step workflows)
- Anthropic Claude + HuggingFace (LLM providers, bring your own)

---

## 📚 Docs

**Workflow-organized — what most developers want, in order:**

1. [`docs/run-locally.md`](docs/run-locally.md) — boot in <10 minutes, no real API keys
2. [`docs/add-a-connector.md`](docs/add-a-connector.md) — one file plus one test
3. [`docs/add-a-workflow.md`](docs/add-a-workflow.md) — author the JSON; let the runtime do the rest
4. [`docs/operate-a-workflow.md`](docs/operate-a-workflow.md) — approve, reject, rerun, replay
5. [`docs/deploy-safely.md`](docs/deploy-safely.md) — production checklist

**Reference material:**

- [`docs/quickstart.md`](docs/quickstart.md) — running in 5 minutes (deeper than `run-locally.md`)
- [`docs/architecture.md`](docs/architecture.md) — how everything fits together
- [`docs/writing-a-connector.md`](docs/writing-a-connector.md) — full BaseConnector reference
- [`docs/contracts/`](docs/contracts/README.md) — v1.0 stable runtime contracts
- [`docs/eval.md`](docs/eval.md) — golden scenario harness
- [`docs/trace.md`](docs/trace.md) — `kazi_trace` CLI
- [`docs/demo-mode.md`](docs/demo-mode.md) — what `KAZI_DEMO_MODE` does
- [`docs/connector-api-reference.md`](docs/connector-api-reference.md) — BaseConnector API
- [`AGENTS.md`](AGENTS.md) — guide for AI coding agents (Claude Code, Codex CLI, Cursor, etc.)
- [`CHANGELOG.md`](CHANGELOG.md) — release history

## ✅ Development Checks

```bash
python Backend/manage.py check
python Backend/manage.py test
flake8 Backend
bandit -r Backend --skip B101
```

## 🤝 Contributing

Contributions welcome — connectors especially.
See [`CONTRIBUTING.md`](CONTRIBUTING.md) for setup and PR expectations.

## 🔒 Security

Do not open public issues for vulnerabilities.
See [`SECURITY.md`](SECURITY.md) for private disclosure.

## 📄 License

MIT. See [`LICENSE`](LICENSE).

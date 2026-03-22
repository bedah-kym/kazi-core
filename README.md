# Kazi Core

![Python](https://img.shields.io/badge/python-3.11-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![CI](https://github.com/bedah-kym/kazi-core/actions/workflows/main.yml/badge.svg)
![Status](https://img.shields.io/badge/status-early%20access-orange)

**Kazi Core** is a self-hostable, open-source agentic engine —
built for teams who want AI agents that call their own tools,
run on their own infrastructure, and keep their data in their own stack.
No vendor lock-in. No data leaving your server.

> **Kazi** is Swahili for *work*. The agent does work for you.

---

## What It Is

Kazi Core gives you the backend runtime for tool-using AI agents:

- A Django-based API/WebSocket service that receives user prompts
- An orchestration loop that plans, executes tools, and returns grounded answers
- A connector framework for plugging in your own services and internal systems

## What It Is Not

- Not a hosted SaaS product
- Not a no-code workflow builder
- Not tied to one model provider or one cloud

---

## Why Not LangChain, OpenAI Agents SDK, or the rest?

Those tools are built for you to use *their* ecosystem.
The APIs, the tooling, the billing, the data — it all flows back to a US company.
You are a user, not an owner.

**Kazi Core is built to be owned:**

- Run it on your own server. Your data doesn't leave.
- Plug in *your* payment rail — M-Pesa, Stripe, whatever your market uses.
- Write connectors for *your* APIs, your local services, your language.
- Security is in the core, not bolted on — prompt injection detection, risk levels, confirmation gates, and audit receipts ship out of the box.
- Community governed. No VC deciding the roadmap.

This matters more outside the US, where the big platforms aren't built for your market, your currency, or your infrastructure.

---

## Project Status

**Early access — actively developed.**
The agent loop, connector registry, and security layer are production-hardened.
The `pip install kazi-core` extraction (standalone library, no Django required) is on the roadmap.
Breaking changes are possible before v1.0.

---

## How Requests Flow

1. User sends a message over HTTP/WebSocket.
2. Kazi assembles context (history, memory, preferences).
3. Agent loop decides whether to answer directly or call tools.
4. Tool executor runs connector actions with safety checks.
5. Results are summarized and streamed back to the user.

## Architecture

```text
Client (HTTP/WebSocket)
  -> ChatConsumer
    -> Context + memory assembly
      -> Agent loop (think -> act -> observe)
        -> Tool executor (risk gates + confirmation)
          -> Connector registry (auto-discovery)
            -> Connectors (built-in + yours)
```

Built-in connectors include travel, messaging, invoicing, and payment integrations.
Disable what you don't need. Add what you do.

---

## Quick Start

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

---

## Create a Connector

Drop a file under `Backend/orchestration/connectors/` — it auto-registers on restart:

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

Full guide: [`docs/writing-a-connector.md`](docs/writing-a-connector.md)

---

## Built-in Connectors

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
| Travel | flights, hotels, buses, transfers, events | Amadeus |
| Reminders | `set_reminder` | Built-in (Celery) |

---

## Repository Layout

```text
Backend/
  orchestration/        # Agent core — start here
    agent_loop.py       # ReAct engine
    tool_executor.py    # Tool dispatch + safety gates
    base_connector.py   # Connector interface
    connector_registry.py  # Auto-discovery
    action_catalog.py   # Tool definitions + risk levels
    security_policy.py  # Injection detection, sanitization
    llm_client.py       # LLM provider abstraction
    connectors/         # Built-in connectors (add yours here)
  chatbot/              # WebSocket consumers, memory, chat transport
  notifications/        # Unified in-app, email, WhatsApp notifications
  workflows/            # Workflow automation (Temporal)
  users/                # Auth, profiles, quotas
```

## Tech Stack

- Python 3.11, Django 5.x (ASGI)
- Django Channels + Redis (WebSocket + real-time)
- PostgreSQL
- Celery + Celery Beat (async tasks + scheduling)
- Temporal (optional — durable multi-step workflows)
- Anthropic Claude + HuggingFace (LLM providers, bring your own)

---

## Development Checks

```bash
python Backend/manage.py check
python Backend/manage.py test
flake8 .
bandit -r . -x ./tests,./venv --skip B101
```

---

## Contributing

Contributions welcome — connectors especially.
See [`CONTRIBUTING.md`](CONTRIBUTING.md) for setup and PR expectations.

## Security

Do not open public issues for vulnerabilities.
See [`SECURITY.md`](SECURITY.md) for private disclosure.

## License

MIT. See [`LICENSE`](LICENSE).

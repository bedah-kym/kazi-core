# Kazi Core

![Python](https://img.shields.io/badge/python-3.11-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![CI](https://github.com/bedah-kym/kazi-core/actions/workflows/main.yml/badge.svg)
![Status](https://img.shields.io/badge/status-early%20access-orange)

Kazi Core is a self-hostable, open-source agentic engine.
Run on your own infrastructure, integrate your own tools, and keep your data in your stack.

> Kazi is Swahili for "work".

## What It Is

- Django-based API and WebSocket runtime for tool-using agents
- ReAct-style orchestration loop (`think -> act -> observe`)
- Connector framework for internal APIs and third-party services
- Built-in safety controls (risk levels, confirmation gates, sanitization)

## What It Is Not

- Not a hosted SaaS
- Not a no-code builder
- Not locked to one model provider or one cloud

## Project Status

**v0.2.0 opens the full orchestration core in OSS.**

This release includes:
- Multi-step planning (`plan_user_request`)
- Deterministic manager verification (`ManagerVerifier`)
- Multi-step execution (`execute_adhoc_workflow`) with Temporal/inline/deferred paths
- Workflow response synthesis (`synthesize_workflow_response_stream`)

Still early access: breaking changes are possible before v1.0.

## How Requests Flow

1. User sends a message over HTTP/WebSocket.
2. Kazi assembles context (history, memory, preferences).
3. The planner decides single action vs multi-step workflow vs clarification.
4. Tool execution runs with safety checks.
5. Results are summarized and streamed back to the user.

## Architecture

```text
Client (HTTP/WebSocket)
  -> ChatConsumer
    -> Context + memory assembly
      -> Planner / Agent loop
        -> Tool executor
          -> Connector registry
            -> Connectors (built-in + custom)
```

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

## Create a Connector

Add a file under `Backend/orchestration/connectors/`:

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

Connector discovery supports:
- Built-in connectors
- Local connectors in `Backend/orchestration/connectors/`
- Entry-point connectors (`kazi.connectors`) from installed packages

Note: the sample `example_connector` is disabled by default.
Set `KAZI_ENABLE_EXAMPLE_CONNECTOR=true` to enable it locally.

## Docs

- `docs/quickstart.md`
- `docs/architecture.md`
- `docs/writing-a-connector.md`
- `docs/connector-api-reference.md`

## Development Checks

```bash
python Backend/manage.py check
python Backend/manage.py test orchestration
flake8 Backend
bandit -r Backend --skip B101
```

## Repository Layout

```text
Backend/
  orchestration/
    agent_loop.py
    workflow_planner.py
    manager_verifier.py
    tool_executor.py
    base_connector.py
    connector_registry.py
    action_catalog.py
    security_policy.py
    llm_client.py
    connectors/
  chatbot/
  notifications/
  workflows/
  users/
```

## Contributing

See `CONTRIBUTING.md`.

## Security

See `SECURITY.md` for private vulnerability reporting.

## License

MIT. See `LICENSE`.

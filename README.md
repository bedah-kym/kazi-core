# Kazi Core

Kazi Core is a self-hostable, open-source agentic engine.
Bring your own tools, run on your own infrastructure, and keep your data in your stack.

## Why Kazi Core

- ReAct-style agent loop (`think -> act -> observe`)
- Pluggable connector system for custom tools
- Built-in safety gates (risk levels, confirmation, sanitization)
- Real-time streaming via WebSockets
- Works with Anthropic and HuggingFace provider paths

## Architecture

```text
Client (HTTP/WebSocket)
  -> ChatConsumer
    -> Context + memory assembly
      -> Agent loop
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

Restart the app and the connector is auto-discovered.

Docs:
- `docs/quickstart.md`
- `docs/architecture.md`
- `docs/writing-a-connector.md`
- `docs/connector-api-reference.md`

## Development Checks

```bash
python Backend/manage.py check
python Backend/manage.py test
flake8 .
bandit -r . -x ./tests,./venv --skip B101
```

## Repository Layout

```text
Backend/
  orchestration/
    agent_loop.py
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

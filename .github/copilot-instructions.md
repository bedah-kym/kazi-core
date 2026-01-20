% Mathia project — guidance for AI coding agents

This file gives concise, actionable guidance so an AI coding agent can be productive in this repository.

1) Big-picture architecture
- Backend is a Django ASGI app using Channels (WebSockets), Redis (channel layer + cache), Celery (workers + beat), and PostgreSQL (DB). See `README.md` and `Backend/Backend/settings.py` for configuration (CHANNEL_LAYERS, CELERY_BROKER_URL, REDIS_URL).  
- The orchestration layer (intent routing / connectors / LLM glue) lives in `Backend/orchestration/`. Key files:
  - `Backend/orchestration/mcp_router.py` — MCPRouter routes parsed intents to connector classes and implements caching and rate-limits.
  - `Backend/orchestration/llm_client.py` — unified LLM client with Anthropic (Claude) first, Hugging Face router fallback. Use `get_llm_client()` to reuse singleton.
  - `Backend/orchestration/base_connector.py` and `Backend/orchestration/connectors/` — connector interface and concrete integrations (WhatsApp, Mailgun, Intersend, etc.).

2) Why things look this way (design notes)
- Single routing surface: MCPRouter centralizes intent validation, connector lookup, and caching so that chat/websocket handlers can remain thin.
- LLM fallback: `llm_client` prefers Anthropic if key present, falls back to HF router. Methods expose both full-response and streaming helpers.
- Connectors are async and should be I/O non-blocking. They often call sync Django ORM via `asgiref.sync.sync_to_async` where needed.

3) Project-specific conventions and patterns
- Connectors: implement an async `execute(self, parameters: Dict, context: Dict) -> Any` and return structured dicts with `status` and `message` (see many examples in `mcp_router.py`). After adding a connector file, register it in `MCPRouter.connectors` map in `mcp_router.py`.
- LLM usage: prefer `get_llm_client()` rather than instantiating LLM clients directly. Use `extract_json()` when expecting structured JSON from an LLM.
- Use Django settings for secrets and feature toggles. The repo loads `.env` from project root (one level above `Backend/`); environment variables control keys like `ANTHROPIC_API_KEY`, `HF_API_TOKEN`, `REDIS_URL`, `CALENDLY_CLIENT_*`.
- Redis: `REDIS_URL` is required in production. The code supports Upstash (`rediss://`) and adjusts SSL handling in `settings.py`.

4) Developer workflows (how to build/run/test)
- Preferred: Docker Compose (recommended by `README.md`):
  - Build and run: `docker-compose up --build` (starts web, db, redis, celery_worker, celery_beat as configured)
  - Run migrations: `docker-compose exec web python manage.py migrate`
- Without Docker (dev): ensure a Redis instance and Postgres (or use sqlite fallback). Start Django ASGI server with `python Backend/manage.py runserver` (or use `daphne`/`uvicorn`) and start celery worker: `celery -A Backend.celery worker --loglevel=info` and beat: `celery -A Backend.celery beat`.
- Tests: run Django tests via `python Backend/manage.py test` inside the `web` container or local venv. Many apps include unit tests (`chatbot/tests.py`, `orchestration/tests.py`, `users/tests.py`).

5) Integrations and external dependencies to watch
- LLMs: Anthropic (CLAUDE) and Hugging Face Router — keys in env. `llm_client.py` shows how both are called and how streaming is handled.
- Calendly: `Backend/orchestration/mcp_router.py` (CalendarConnector) uses a `CalendlyProfile` on the User model; credentials in `CALENDLY_CLIENT_ID`/`CALENDLY_CLIENT_SECRET`.
- Third-party keys: OPENWEATHER_API_KEY, GIPHY_API_KEY, EXCHANGE_RATE_API_KEY — connectors gracefully return errors when missing.
- Channels & Celery rely on Redis. If Redis is down, realtime features are degraded but HTTP views still function.

6) Common change patterns and quick examples
- Adding a connector:
  1. Create `Backend/orchestration/connectors/<your_connector>.py` implementing async `execute(parameters, context)`.
  2. Register the connector in `MCPRouter.connectors` mapping in `mcp_router.py` (key = action name used by intent parser).
  3. Add tests in `Backend/orchestration/tests.py` or the app's `tests.py`.

- Modifying LLM behavior: update `Backend/orchestration/llm_client.py` but keep `generate_text` and `stream_text` semantics so callers in `mcp_router.py` and `chatbot` keep working.

7) Where to look first when debugging
- WebSocket behavior & encryption: `Backend/chatbot/consumers.py` (presence, room handling, key rotation).  
- Background tasks & moderation: `Backend/chatbot/tasks.py` (Celery tasks, moderation batch logic referenced in `CELERY_BEAT_SCHEDULE` in `settings.py`).
- Intent parsing: `Backend/orchestration/intent_parser.py` and routing in `mcp_router.py`.

8) Safety & operational notes
- Secrets: `.env` in project root is used in dev — do not commit secrets. Production requires `REDIS_URL` and appropriate DB URL.
- Be careful when changing Celery task timeouts or prefetch multipliers defined in `settings.py` (affects throughput and concurrency).

If anything in these notes is unclear or you want additional examples (e.g., a skeleton connector, test template, or run commands for Windows PowerShell), tell me which section to expand and I'll iterate.  

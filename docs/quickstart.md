# Quick Start

Get Kazi running locally in under 5 minutes.

## Prerequisites

- Docker and Docker Compose
- At least one LLM API key (Anthropic recommended)

## 1. Clone and Configure

```bash
git clone https://github.com/bedah-kym/django-chat.git
cd django-chat
```

Create a `.env` file in the project root:

```bash
# Required
DJANGO_SECRET_KEY=change-me-to-a-random-string
DJANGO_DEBUG=true
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=http://localhost:8000
DATABASE_URL=postgres://mathia_user:mathia_password@db:5432/mathia_db
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0

# LLM provider (at least one)
ANTHROPIC_API_KEY=sk-ant-...
# HF_API_TOKEN=hf_...      # optional fallback
```

## 2. Start Services

```bash
docker compose up --build -d db redis web celery_worker celery_beat
```

## 3. Set Up the Database

```bash
docker compose exec web python Backend/manage.py migrate
docker compose exec web python Backend/manage.py createsuperuser
```

## 4. Open the App

Go to `http://localhost:8000`. Log in with your superuser credentials.

## 5. Try It

Open a chatroom and send a message like:
- "What's the weather in Nairobi?"
- "Convert 100 USD to KES"
- "Search for flights from Nairobi to Mombasa"

The agent will call the appropriate tools and respond.

## Optional: Temporal (Durable Workflows)

```bash
docker compose -f docker-compose.temporal.yml up -d
docker compose up -d temporal_worker
```

## Local Development (Without Docker)

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv/Scripts/activate on Windows
pip install -r requirements.txt

# Set env vars (same as above, adjust DATABASE_URL and REDIS_URL for local services)
export DJANGO_SECRET_KEY=dev-secret
export DJANGO_DEBUG=true
export ANTHROPIC_API_KEY=sk-ant-...

python Backend/manage.py migrate
python Backend/manage.py runserver
```

In separate terminals:
```bash
celery -A Backend worker -l info
celery -A Backend beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

## Next Steps

- Read `docs/architecture.md` to understand how the system works
- Read `docs/writing-a-connector.md` to build your first plugin
- Check `Backend/orchestration/connectors/example_connector.py` for a minimal template

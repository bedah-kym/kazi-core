#!/usr/bin/env bash
# v0.4 demo script — boots Kazi Core in demo mode, seeds the canonical
# follow-up-email workflow, and prints the curl commands needed to drive
# it through the human-gated runtime end-to-end.
#
# Goal: from a clean clone, get to a paused workflow waiting on operator
# approval in under 90 seconds. No real API keys required.
#
# See: examples/workflows/follow_up_email/README.md

set -euo pipefail

# Resolve repo root (this script lives in scripts/).
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DEMO_USER="${KAZI_DEMO_USER:-demo}"
DEMO_PASSWORD="${KAZI_DEMO_PASSWORD:-demo-password-change-me}"
DEMO_EMAIL="${KAZI_DEMO_EMAIL:-demo@example.com}"
COMPOSE_SERVICES="db redis web celery_worker celery_beat"

# Color helpers — falls back to plain text if not a TTY.
if [[ -t 1 ]]; then
  CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RESET='\033[0m'
else
  CYAN=''; GREEN=''; YELLOW=''; RESET=''
fi

step() { echo -e "${CYAN}==>${RESET} $*"; }
ok()   { echo -e "${GREEN}OK${RESET} $*"; }
warn() { echo -e "${YELLOW}!!${RESET} $*"; }

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required. Install Docker Desktop or your distro's docker package." >&2
  exit 1
fi

step "Booting Kazi Core in KAZI_DEMO_MODE=true"
KAZI_DEMO_MODE=true docker compose up --build -d $COMPOSE_SERVICES
ok "Stack started."

step "Waiting for the web service to be reachable on http://localhost:8000"
for i in {1..30}; do
  if curl --silent --fail --max-time 2 http://localhost:8000/health/ >/dev/null 2>&1 \
     || curl --silent --fail --max-time 2 http://localhost:8000/ >/dev/null 2>&1; then
    ok "Web service is up."
    break
  fi
  if [[ $i -eq 30 ]]; then
    warn "Web service did not respond in 60s. Check 'docker compose logs web' and retry."
    exit 1
  fi
  sleep 2
done

step "Running migrations"
docker compose exec -T web python Backend/manage.py migrate --noinput
ok "Migrations applied."

step "Ensuring demo user '${DEMO_USER}' exists"
docker compose exec -T -e DEMO_USER="$DEMO_USER" -e DEMO_PASSWORD="$DEMO_PASSWORD" -e DEMO_EMAIL="$DEMO_EMAIL" \
  web python Backend/manage.py shell <<'PY'
import os
from django.contrib.auth import get_user_model
User = get_user_model()
username = os.environ["DEMO_USER"]
user, created = User.objects.get_or_create(
    username=username,
    defaults={"email": os.environ["DEMO_EMAIL"]},
)
user.set_password(os.environ["DEMO_PASSWORD"])
user.is_staff = True
user.save()
print(f"{'created' if created else 'updated'} user {username}")
PY
ok "Demo user ready."

step "Seeding the canonical follow-up-email workflow"
docker compose exec -T web python Backend/manage.py seed_demo_workflow --user "$DEMO_USER"
ok "Workflow seeded."

cat <<EOF

${GREEN}===============================================================${RESET}
  Demo stack is up and the workflow is seeded.
${GREEN}===============================================================${RESET}

  Demo user:     ${DEMO_USER}
  Demo password: ${DEMO_PASSWORD}
  Web URL:       http://localhost:8000/
  Workflow API:  http://localhost:8000/api/workflows/

Next, drive the human-gated loop:

  ${CYAN}1. Find your workflow id${RESET} (visible in the seed output above), then start a run:
     curl -X POST http://localhost:8000/api/workflows/<workflow_id>/run/ \\
       -H "Content-Type: application/json" \\
       -d '{"trigger_data": {}}'

  ${CYAN}2. Inspect the execution${RESET} — it should be paused on 'send_follow_up':
     curl http://localhost:8000/api/workflows/executions/<execution_id>/

  ${CYAN}3. Approve${RESET}:
     curl -X POST http://localhost:8000/api/workflows/executions/<execution_id>/approve/ \\
       -H "Content-Type: application/json" \\
       -d '{"note": "ok send it"}'

  ${CYAN}4. Replay-safety check${RESET} — try rerun from each step and watch which one is refused:
     curl -X POST http://localhost:8000/api/workflows/executions/<execution_id>/rerun/ \\
       -H "Content-Type: application/json" \\
       -d '{"from_step": "send_follow_up"}'    # blocked: not safe to replay
     curl -X POST http://localhost:8000/api/workflows/executions/<execution_id>/rerun/ \\
       -H "Content-Type: application/json" \\
       -d '{"from_step": "draft_follow_up"}'   # allowed

Full walkthrough: examples/workflows/follow_up_email/README.md
Production warning: KAZI_DEMO_MODE=true must NEVER be set in production.
EOF

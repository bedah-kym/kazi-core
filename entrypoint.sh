#!/bin/sh
set -e

# Wait for database
python /app/wait_for_db.py

cd Backend

# Run migrations and collect static files unless explicitly skipped
if [ "${SKIP_MIGRATIONS:-0}" != "1" ]; then
    python manage.py migrate --no-input
    python manage.py seed_mathia
    python manage.py collectstatic --no-input
fi

exec "$@"

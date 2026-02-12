#!/bin/sh

# Wait for database
python /app/wait_for_db.py

cd Backend

# Run migrations and collect static files unless explicitly skipped
if [ "${SKIP_MIGRATIONS:-0}" != "1" ]; then
    python manage.py migrate
    python manage.py collectstatic --no-input
fi

exec "$@"

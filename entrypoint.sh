#!/bin/sh

# Wait for database
python /app/wait_for_db.py

cd Backend

# Run migrations
python manage.py migrate

# Collect static files
python manage.py collectstatic --no-input

exec "$@"

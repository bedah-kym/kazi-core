#!/bin/sh
set -e

cd /app/Backend
exec celery -A Backend worker -l info --concurrency ${CELERY_CONCURRENCY:-4}

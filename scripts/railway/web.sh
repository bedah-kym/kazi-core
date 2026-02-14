#!/bin/sh
set -e

cd /app/Backend
exec gunicorn Backend.asgi:application \
  -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:${PORT:-8000} \
  --workers ${WEB_CONCURRENCY:-1} \
  --timeout ${GUNICORN_TIMEOUT:-120}

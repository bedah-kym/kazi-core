#!/bin/sh
set -e

cd /app/Backend
exec python manage.py start_temporal_worker

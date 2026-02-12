#!/bin/sh
set -e

cd /app/Backend
exec celery -A Backend beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler

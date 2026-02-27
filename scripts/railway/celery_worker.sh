#!/bin/sh
set -e

cd /app/Backend

POOL=${CELERY_POOL:-prefork}
MAX_TASKS=${CELERY_WORKER_MAX_TASKS_PER_CHILD:-200}
MAX_MEM=${CELERY_WORKER_MAX_MEMORY_PER_CHILD:-250000}

if [ -n "$CELERY_AUTOSCALE" ]; then
  SCALE_ARGS="--autoscale ${CELERY_AUTOSCALE}"
else
  SCALE_ARGS="--concurrency ${CELERY_CONCURRENCY:-1}"
fi

exec celery -A Backend worker -l info --pool ${POOL} ${SCALE_ARGS} \
  --max-tasks-per-child ${MAX_TASKS} \
  --max-memory-per-child ${MAX_MEM}

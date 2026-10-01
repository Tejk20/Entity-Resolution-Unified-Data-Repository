#!/usr/bin/env bash
#
# Consolidated entrypoint for the single all-in-one Render service.
#
# Runs, in order:
#   1. alembic migrations (blocking, so the schema is ready before anything else)
#   2. Celery worker  (background)
#   3. Celery beat    (background)
#   4. Uvicorn        (foreground -> PID 1, owns container lifetime)
#
# The free tier gives 0.5 GB RAM / 1 CPU, so the worker is pinned to a single
# prefork process. Celery is configured with task_acks_late +
# task_reject_on_worker_lost, so if the container is killed mid-task the broker
# redelivers it.
set -e
cd "$(dirname "$0")/.."

# --- 1. database migrations -------------------------------------------------
# Non-fatal: the FastAPI lifespan hook also applies migrations (and create_all)
# when RUN_MIGRATIONS_ON_STARTUP is true, and the service should still boot to
# surface the real error on /health rather than crash-loop before it can listen.
alembic upgrade head || echo "start.sh: alembic upgrade failed; deferring to app startup" >&2

# --- 2. celery worker -------------------------------------------------------
# Explicit queue list: the app declares ingest/resolve/default and routes on it.
celery -A app.workers.celery_app:celery_app worker \
  --loglevel="${LOG_LEVEL:-info}" \
  --concurrency=1 \
  -Q ingest,resolve,default &
WORKER_PID=$!

# --- 3. celery beat ---------------------------------------------------------
celery -A app.workers.celery_app:celery_app beat \
  --loglevel="${LOG_LEVEL:-info}" &
BEAT_PID=$!

echo "start.sh: celery worker pid=${WORKER_PID} beat pid=${BEAT_PID}"

# --- 4. FastAPI -------------------------------------------------------------
# exec so uvicorn replaces this shell and becomes PID 1, receiving SIGTERM
# directly from the platform. The backgrounded celery processes are reparented
# to it and torn down with the container.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
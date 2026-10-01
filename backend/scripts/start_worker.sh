#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

exec celery -A app.workers.celery_app:celery_app worker \
  --loglevel="${LOG_LEVEL:-info}" \
  --concurrency=1 \
  -Q ingest,resolve,default
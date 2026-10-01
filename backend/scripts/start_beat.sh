#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

exec celery -A app.workers.celery_app:celery_app beat --loglevel="${LOG_LEVEL:-info}"
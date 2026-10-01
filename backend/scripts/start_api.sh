#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Explicit migrations on every deploy (also guarded on boot by RUN_MIGRATIONS_ON_STARTUP).
alembic upgrade head

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
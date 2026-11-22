#!/bin/sh
set -eu
attempt=0
until alembic upgrade head; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 20 ]; then
    echo "alembic upgrade failed" >&2
    exit 1
  fi
  sleep 1
done
if [ "${NADOU_ALLOW_DEV_SEED:-0}" = "1" ]; then
  python -m nadou seed-demo
fi
exec uvicorn nadou.main:app --host 0.0.0.0 --port 8000

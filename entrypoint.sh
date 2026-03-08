#!/bin/sh
set -e

cd /app

echo ">>> Running database migrations..."
alembic upgrade head

echo ">>> Starting Collei..."
exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1

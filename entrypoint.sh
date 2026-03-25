#!/bin/sh
set -e

cd /app

# 将内置数据文件复制到数据卷目录（仅首次或文件不存在时）
DATA_DIR="${COLLEI_DATA_DIR:-/data}"
for f in /app/data/*.mmdb; do
  [ -f "$f" ] || continue
  name=$(basename "$f")
  if [ ! -f "$DATA_DIR/$name" ]; then
    echo ">>> Copying $name to $DATA_DIR/"
    cp "$f" "$DATA_DIR/$name"
  fi
done

echo ">>> Running database migrations..."
alembic upgrade head

echo ">>> Starting Collei..."
exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1

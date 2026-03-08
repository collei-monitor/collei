#!/bin/sh
# Collei 后端启动入口
# 1. 运行数据库迁移
# 2. 启动 uvicorn 服务
set -e

# 确保 /app 在 PYTHONPATH 中，使 alembic 能正确导入 app 包
export PYTHONPATH="/app:${PYTHONPATH:-}"

# 确保数据目录存在
mkdir -p /data

echo "[collei] 正在运行数据库迁移..."
alembic upgrade head

echo "[collei] 启动 Collei 后端服务..."
exec uvicorn main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --proxy-headers \
  --forwarded-allow-ips='*'

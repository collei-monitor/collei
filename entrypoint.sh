#!/bin/sh
set -e

cd /app

DATA_DIR="${COLLEI_DATA_DIR:-/data}"
SECRETS_FILE="$DATA_DIR/.secrets"

# ── 1. 修正数据卷目录权限（宿主挂载可能为 root 所有）──────────────────────
echo ">>> Ensuring /data ownership..."
chown -R collei:collei "$DATA_DIR"

# ── 2. 复制内置数据文件（仅首次或文件不存在时）────────────────────────────
for f in /app/data/*.mmdb; do
  [ -f "$f" ] || continue
  name=$(basename "$f")
  if [ ! -f "$DATA_DIR/$name" ]; then
    echo ">>> Copying $name to $DATA_DIR/"
    cp "$f" "$DATA_DIR/$name"
    chown collei:collei "$DATA_DIR/$name"
  fi
done

# ── 3. 密钥自动生成 / 持久化 ──────────────────────────────────────────────
#   优先级: 环境变量 > .secrets 文件 > 自动生成
if [ -z "$COLLEI_SECRET_KEY" ] || [ -z "$COLLEI_CA_MASTER_KEY" ]; then
  if [ -f "$SECRETS_FILE" ]; then
    echo ">>> Loading secrets from $SECRETS_FILE"
    . "$SECRETS_FILE"
    export COLLEI_SECRET_KEY COLLEI_CA_MASTER_KEY
  else
    echo ">>> Generating new secrets..."
    _SK=$(python -c "import secrets; print(secrets.token_urlsafe(64))")
    _CK=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
    COLLEI_SECRET_KEY="${COLLEI_SECRET_KEY:-$_SK}"
    COLLEI_CA_MASTER_KEY="${COLLEI_CA_MASTER_KEY:-$_CK}"
    export COLLEI_SECRET_KEY COLLEI_CA_MASTER_KEY

    cat > "$SECRETS_FILE" <<EOF
COLLEI_SECRET_KEY='${COLLEI_SECRET_KEY}'
COLLEI_CA_MASTER_KEY='${COLLEI_CA_MASTER_KEY}'
EOF
    chmod 600 "$SECRETS_FILE"
    chown collei:collei "$SECRETS_FILE"
    echo ">>> Secrets saved to $SECRETS_FILE"
  fi
fi

# ── 4. 数据库迁移 ─────────────────────────────────────────────────────────
echo ">>> Running database migrations..."
alembic upgrade head

# ── 5. 降权启动 ───────────────────────────────────────────────────────────
echo ">>> Starting Collei (port 22333)..."
exec gosu collei uvicorn main:app --host 0.0.0.0 --port 22333 --workers 1

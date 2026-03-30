#!/bin/sh
set -e

cd /app

DATA_DIR="${COLLEI_DATA_DIR:-/data}"
SECRETS_FILE="$DATA_DIR/.secrets"

# ── 1. 复制内置数据文件（仅首次或文件不存在时）────────────────────────────
#   注意：必须在 chown 之前执行，因为容器 cap_drop ALL 后 root 缺少
#   DAC_OVERRIDE，无法向非 root 目录写入。
for f in /app/data/*.mmdb; do
  [ -f "$f" ] || continue
  name=$(basename "$f")
  if [ ! -f "$DATA_DIR/$name" ]; then
    echo ">>> Copying $name to $DATA_DIR/"
    cp "$f" "$DATA_DIR/$name"
  fi
done

# ── 2. 密钥自动生成 / 持久化 ──────────────────────────────────────────────
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
    echo ">>> Secrets saved to $SECRETS_FILE"
  fi
fi

# ── 3. 数据库迁移 ─────────────────────────────────────────────────────────
echo ">>> Running database migrations..."
alembic upgrade head

# ── 4. 修正数据卷权限并降权启动 ───────────────────────────────────────────
#   chown 放在最后，确保之前所有写操作（cp/secrets/migrate）均以 root 完成。
echo ">>> Fixing /data ownership & starting Collei (port 22333)..."
chown -R collei:collei "$DATA_DIR"
exec gosu collei uvicorn main:app --host 0.0.0.0 --port 22333 --workers 1

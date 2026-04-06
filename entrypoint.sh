#!/bin/sh
set -e

cd /app

DATA_DIR="${COLLEI_DATA_DIR:-/data}"
SECRETS_FILE="$DATA_DIR/.secrets"

# ── 0. 夺回数据卷所有权 ──────────────────────────────────────────────────
#   容器 cap_drop ALL 后 root 缺少 DAC_OVERRIDE，无法读写非 root 文件。
#   靠保留的 CHOWN 能力先将 /data 夺回 root，操作完毕后再交给 collei。
chown -R root:root "$DATA_DIR"

# ── 1. 复制内置数据文件（仅首次或文件不存在时）────────────────────────────
#   注意：必须在 chown 之前执行，因为容器 cap_drop ALL 后 root 缺少
#   DAC_OVERRIDE，无法向非 root 目录写入。
for f in /app/data/*.mmdb /app/data/default.ico; do
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

# ── 3. 恢复检测 ──────────────────────────────────────────────────────────────
#   如果存在 .restore-pending 标记文件，执行备份恢复流程。
#   流程：备份当前数据 → 覆盖恢复文件 → 删除暂存 → 正常迁移。
RESTORE_DIR="$DATA_DIR/.restore"
RESTORE_PENDING="$DATA_DIR/.restore-pending"
PRE_RESTORE_BACKUP="$DATA_DIR/.pre-restore-backup"

if [ -f "$RESTORE_PENDING" ] && [ -d "$RESTORE_DIR" ]; then
  echo ">>> [RESTORE] 检测到待恢复备份，开始恢复..."

  # 备份当前数据（以防恢复后需要回退）
  rm -rf "$PRE_RESTORE_BACKUP"
  mkdir -p "$PRE_RESTORE_BACKUP"
  for f in collei.db .secrets ssh_ca_key.enc ssh_ca_key.pub ssh_ca_key_old.pub; do
    [ -f "$DATA_DIR/$f" ] && cp "$DATA_DIR/$f" "$PRE_RESTORE_BACKUP/$f"
  done
  echo ">>> [RESTORE] 当前数据已备份到 $PRE_RESTORE_BACKUP"

  # 恢复文件覆盖（跳过 backup_meta.json）
  for f in "$RESTORE_DIR"/*; do
    name=$(basename "$f")
    [ "$name" = "backup_meta.json" ] && continue
    cp "$f" "$DATA_DIR/$name"
    echo ">>> [RESTORE] 已恢复: $name"
  done

  # 如果恢复的 .secrets 文件存在，重新加载密钥
  if [ -f "$RESTORE_DIR/.secrets" ]; then
    echo ">>> [RESTORE] 重新加载恢复的密钥..."
    . "$DATA_DIR/.secrets"
    export COLLEI_SECRET_KEY COLLEI_CA_MASTER_KEY
  fi

  # 清理暂存
  rm -rf "$RESTORE_DIR"
  rm -f "$RESTORE_PENDING"
  echo ">>> [RESTORE] 恢复完成，继续启动..."
fi

# ── 4. 数据库迁移 ─────────────────────────────────────────────────────────
echo ">>> Running database migrations..."
alembic upgrade head

# ── 5. 修正数据卷权限并降权启动 ───────────────────────────────────────────
#   chown 放在最后，确保之前所有写操作（cp/secrets/migrate）均以 root 完成。
#   .secrets 保留 root 所有：容器 cap_drop ALL 后 root 缺少 DAC_OVERRIDE，
#   重启时需要以 root 身份读取该文件。
echo ">>> Fixing /data ownership & starting Collei (port 22333)..."
chown -R collei:collei "$DATA_DIR"
[ -f "$SECRETS_FILE" ] && chown root:root "$SECRETS_FILE"
exec su-exec collei uvicorn main:app --host 0.0.0.0 --port 22333 --workers 1

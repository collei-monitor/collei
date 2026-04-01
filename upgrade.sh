#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Collei — 裸机升级脚本
# 用法: sudo bash upgrade.sh [选项]
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

# ── 默认值 ────────────────────────────────────────────────────────────────────
INSTALL_DIR="/opt/collei"
DATA_DIR="/var/lib/collei"
FRONTEND_VERSION="latest"
FRONTEND_REPO="https://github.com/collei-monitor/collei-web"
SKIP_RESTART=false

# ── 颜色 ─────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }
step()  { echo -e "\n${CYAN}══════ $* ══════${NC}"; }

# ── 参数解析 ──────────────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
  case "$1" in
    --install-dir)      INSTALL_DIR="$2"; shift 2 ;;
    --data-dir)         DATA_DIR="$2"; shift 2 ;;
    --frontend-version) FRONTEND_VERSION="$2"; shift 2 ;;
    --skip-restart)     SKIP_RESTART=true; shift ;;
    -h|--help)
      echo "用法: sudo bash upgrade.sh [选项]"
      echo ""
      echo "选项:"
      echo "  --install-dir DIR       安装目录 (默认: /opt/collei)"
      echo "  --data-dir DIR          数据目录 (默认: /var/lib/collei)"
      echo "  --frontend-version VER  前端版本 (默认: latest)"
      echo "  --skip-restart          跳过服务重启"
      echo "  -h, --help              显示帮助"
      exit 0
      ;;
    *) error "未知参数: $1（使用 --help 查看帮助）" ;;
  esac
done

# ── 权限检查 ──────────────────────────────────────────────────────────────────
[ "$(id -u)" -eq 0 ] || error "请以 root 权限运行: sudo bash upgrade.sh"

# ── 环境检查 ──────────────────────────────────────────────────────────────────
[ -d "$INSTALL_DIR/.git" ] || error "$INSTALL_DIR 不存在或不是 git 仓库，请先执行 deploy.sh"
[ -d "$INSTALL_DIR/.venv" ] || error "$INSTALL_DIR/.venv 不存在，请先执行 deploy.sh"

# 确保 UV 在 PATH 中
export PATH="$HOME/.local/bin:/root/.local/bin:$PATH"
command -v uv >/dev/null 2>&1 || error "UV 未安装，请先执行 deploy.sh 或手动安装: curl -LsSf https://astral.sh/uv/install.sh | sh"

# ══════════════════════════════════════════════════════════════════════════════
step "1/5 备份数据库"
# ══════════════════════════════════════════════════════════════════════════════

DB_FILE="$DATA_DIR/collei.db"
if [ -f "$DB_FILE" ]; then
  BACKUP="$DATA_DIR/collei.db.bak.$(date +%Y%m%d%H%M%S)"
  cp "$DB_FILE" "$BACKUP"
  info "数据库已备份: $BACKUP"
else
  warn "未找到数据库文件 $DB_FILE，跳过备份"
fi

# ══════════════════════════════════════════════════════════════════════════════
step "2/5 拉取最新后端代码"
# ══════════════════════════════════════════════════════════════════════════════

cd "$INSTALL_DIR"

# 记录当前版本
OLD_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

git pull --ff-only
NEW_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

if [ "$OLD_COMMIT" = "$NEW_COMMIT" ]; then
  info "后端代码已是最新 ($NEW_COMMIT)"
else
  info "后端代码已更新: $OLD_COMMIT → $NEW_COMMIT"
fi

# ══════════════════════════════════════════════════════════════════════════════
step "3/5 更新 Python 依赖"
# ══════════════════════════════════════════════════════════════════════════════

info "同步依赖..."
uv pip install --quiet .
info "依赖更新完成"

# ══════════════════════════════════════════════════════════════════════════════
step "4/5 更新前端"
# ══════════════════════════════════════════════════════════════════════════════

if [ "$FRONTEND_VERSION" = "latest" ]; then
  info "获取最新前端版本号..."
  REDIRECT_URL=$(curl -fsS -o /dev/null -w '%{redirect_url}' \
    "${FRONTEND_REPO}/releases/latest" 2>/dev/null || true)
  if [ -n "$REDIRECT_URL" ]; then
    FRONTEND_VERSION=$(basename "$REDIRECT_URL")
  else
    error "无法获取最新前端版本。请使用 --frontend-version 手动指定。"
  fi
fi

TARBALL_URL="${FRONTEND_REPO}/releases/download/${FRONTEND_VERSION}/collei-web-${FRONTEND_VERSION}.tar.gz"
info "下载前端 ${FRONTEND_VERSION}..."
rm -rf "$INSTALL_DIR/frontend/dist"
mkdir -p "$INSTALL_DIR/frontend/dist"
curl -fsSL "$TARBALL_URL" | tar xz -C "$INSTALL_DIR/frontend/dist"
echo "$FRONTEND_VERSION" > "$INSTALL_DIR/frontend/dist/.version"
info "前端已更新到 ${FRONTEND_VERSION}"

# ══════════════════════════════════════════════════════════════════════════════
step "5/5 数据库迁移 + 重启服务"
# ══════════════════════════════════════════════════════════════════════════════

# 加载环境变量
if [ -f "$INSTALL_DIR/.env" ]; then
  set -a
  . "$INSTALL_DIR/.env"
  set +a
fi

info "执行 alembic upgrade head..."
"$INSTALL_DIR/.venv/bin/alembic" upgrade head
info "数据库迁移完成"

# 复制新增的 GeoIP 数据
for f in "$INSTALL_DIR"/data/*.mmdb; do
  [ -f "$f" ] || continue
  name=$(basename "$f")
  if [ ! -f "$DATA_DIR/$name" ]; then
    cp "$f" "$DATA_DIR/$name"
    info "已复制新增数据文件: $name"
  fi
done

if [ "$SKIP_RESTART" = true ]; then
  warn "已跳过服务重启，请手动执行: sudo systemctl restart collei"
else
  if systemctl is-active --quiet collei 2>/dev/null; then
    systemctl restart collei
    info "collei.service 已重启"
  else
    warn "collei.service 未运行，尝试启动..."
    systemctl start collei
    info "collei.service 已启动"
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Collei 升级完成！${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  后端:  ${OLD_COMMIT} → ${NEW_COMMIT}"
echo -e "  前端:  ${FRONTEND_VERSION}"
if [ -n "${BACKUP:-}" ]; then
  echo -e "  备份:  ${BACKUP}"
fi
echo ""
echo -e "  查看状态: ${CYAN}systemctl status collei${NC}"
echo -e "  查看日志: ${CYAN}journalctl -u collei -f${NC}"
echo ""

#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Collei — Linux 一键部署脚本（UV 驱动）
# 用法: sudo bash deploy.sh [选项]
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

# ── 默认值 ────────────────────────────────────────────────────────────────────
INSTALL_DIR="/opt/collei"
DATA_DIR="/var/lib/collei"
PORT=22333
FRONTEND_VERSION="latest"
SKIP_SYSTEMD=false
REPO_URL="https://github.com/collei-monitor/collei"
FRONTEND_REPO="https://github.com/collei-monitor/collei-web"

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
    --port)             PORT="$2"; shift 2 ;;
    --frontend-version) FRONTEND_VERSION="$2"; shift 2 ;;
    --skip-systemd)     SKIP_SYSTEMD=true; shift ;;
    -h|--help)
      echo "用法: sudo bash deploy.sh [选项]"
      echo ""
      echo "选项:"
      echo "  --install-dir DIR       安装目录 (默认: /opt/collei)"
      echo "  --data-dir DIR          数据目录 (默认: /var/lib/collei)"
      echo "  --port PORT             监听端口 (默认: 22333)"
      echo "  --frontend-version VER  前端版本 (默认: latest)"
      echo "  --skip-systemd          跳过 systemd 服务配置"
      echo "  -h, --help              显示帮助"
      exit 0
      ;;
    *) error "未知参数: $1（使用 --help 查看帮助）" ;;
  esac
done

# ── 权限检查 ──────────────────────────────────────────────────────────────────
[ "$(id -u)" -eq 0 ] || error "请以 root 权限运行: sudo bash deploy.sh"

# ══════════════════════════════════════════════════════════════════════════════
step "1/8 检测系统环境"
# ══════════════════════════════════════════════════════════════════════════════

ARCH=$(uname -m)
info "架构: $ARCH"

if [ -f /etc/os-release ]; then
  . /etc/os-release
  info "系统: $PRETTY_NAME"
else
  warn "无法检测发行版，继续执行..."
fi

# 检测包管理器
if command -v apt-get >/dev/null 2>&1; then
  PKG_MGR="apt"
elif command -v dnf >/dev/null 2>&1; then
  PKG_MGR="dnf"
elif command -v yum >/dev/null 2>&1; then
  PKG_MGR="yum"
elif command -v pacman >/dev/null 2>&1; then
  PKG_MGR="pacman"
else
  warn "未检测到支持的包管理器，跳过系统依赖安装"
  PKG_MGR="none"
fi

# 安装基础依赖（git、curl）
install_pkg() {
  case "$PKG_MGR" in
    apt)    apt-get update -qq && apt-get install -y -qq "$@" ;;
    dnf)    dnf install -y -q "$@" ;;
    yum)    yum install -y -q "$@" ;;
    pacman) pacman -Sy --noconfirm "$@" ;;
    none)   warn "请手动安装: $*" ;;
  esac
}

for cmd in git curl; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    info "安装 $cmd..."
    install_pkg "$cmd"
  fi
done

# ══════════════════════════════════════════════════════════════════════════════
step "2/8 安装 UV"
# ══════════════════════════════════════════════════════════════════════════════

if command -v uv >/dev/null 2>&1; then
  info "UV 已安装: $(uv --version)"
else
  info "正在安装 UV..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # 确保 UV 在 PATH 中
  export PATH="$HOME/.local/bin:$PATH"
  if ! command -v uv >/dev/null 2>&1; then
    export PATH="/root/.local/bin:$PATH"
  fi
  info "UV 安装完成: $(uv --version)"
fi

# ══════════════════════════════════════════════════════════════════════════════
step "3/8 克隆后端代码"
# ══════════════════════════════════════════════════════════════════════════════

if [ -d "$INSTALL_DIR/.git" ]; then
  info "检测到已有仓库，执行 git pull..."
  cd "$INSTALL_DIR"
  git pull --ff-only
else
  info "克隆 $REPO_URL → $INSTALL_DIR"
  git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
  cd "$INSTALL_DIR"
fi

# ══════════════════════════════════════════════════════════════════════════════
step "4/8 创建 Python 虚拟环境并安装依赖"
# ══════════════════════════════════════════════════════════════════════════════

cd "$INSTALL_DIR"

if [ ! -d ".venv" ]; then
  info "创建虚拟环境 (Python 3.12)..."
  uv venv --python 3.12
fi

info "安装依赖..."
uv pip install --quiet .

# ══════════════════════════════════════════════════════════════════════════════
step "5/8 下载前端"
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
mkdir -p "$INSTALL_DIR/frontend/dist"
curl -fsSL "$TARBALL_URL" | tar xz -C "$INSTALL_DIR/frontend/dist"
echo "$FRONTEND_VERSION" > "$INSTALL_DIR/frontend/dist/.version"
info "前端已解压到 $INSTALL_DIR/frontend/dist"

# ══════════════════════════════════════════════════════════════════════════════
step "6/8 配置数据目录与密钥"
# ══════════════════════════════════════════════════════════════════════════════

mkdir -p "$DATA_DIR"

# 复制 GeoIP 数据库
for f in "$INSTALL_DIR"/data/*.mmdb; do
  [ -f "$f" ] || continue
  name=$(basename "$f")
  if [ ! -f "$DATA_DIR/$name" ]; then
    cp "$f" "$DATA_DIR/$name"
    info "已复制 $name → $DATA_DIR/"
  fi
done

# 生成密钥
SECRETS_FILE="$DATA_DIR/.secrets"
if [ -f "$SECRETS_FILE" ]; then
  info "密钥文件已存在，跳过生成"
else
  info "生成 SECRET_KEY 和 CA_MASTER_KEY..."
  _SK=$("$INSTALL_DIR/.venv/bin/python" -c "import secrets; print(secrets.token_urlsafe(64))")
  _CK=$("$INSTALL_DIR/.venv/bin/python" -c "import secrets; print(secrets.token_urlsafe(32))")
  cat > "$SECRETS_FILE" <<EOF
COLLEI_SECRET_KEY='${_SK}'
COLLEI_CA_MASTER_KEY='${_CK}'
EOF
  chmod 600 "$SECRETS_FILE"
  info "密钥已保存到 $SECRETS_FILE (chmod 600)"
fi

# 从 .secrets 读取密钥值，生成 .env
. "$SECRETS_FILE"

# 生成 .env（如不存在）
ENV_FILE="$INSTALL_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  info "生成 .env 配置文件..."
  ADMIN_PASS=$("$INSTALL_DIR/.venv/bin/python" -c "import secrets; print(secrets.token_urlsafe(12))")
  cat > "$ENV_FILE" <<EOF
# Collei 配置文件 — 由 deploy.sh 自动生成
COLLEI_SECRET_KEY=${COLLEI_SECRET_KEY}
COLLEI_CA_MASTER_KEY=${COLLEI_CA_MASTER_KEY}
COLLEI_DATABASE_URL=sqlite+aiosqlite:///${DATA_DIR}/collei.db
COLLEI_DATA_DIR=${DATA_DIR}
COLLEI_DEFAULT_ADMIN_USERNAME=admin
COLLEI_DEFAULT_ADMIN_PASSWORD=${ADMIN_PASS}
COLLEI_DEBUG=false
COLLEI_COOKIE_SECURE=false
EOF
  chmod 600 "$ENV_FILE"
  info "管理员密码: ${ADMIN_PASS}（请登录后立即修改）"
else
  info ".env 已存在，跳过生成"
fi

# ══════════════════════════════════════════════════════════════════════════════
step "7/8 数据库迁移"
# ══════════════════════════════════════════════════════════════════════════════

cd "$INSTALL_DIR"
info "执行 alembic upgrade head..."
set -a; . "$INSTALL_DIR/.env"; set +a
"$INSTALL_DIR/.venv/bin/alembic" upgrade head
info "数据库迁移完成"

# ══════════════════════════════════════════════════════════════════════════════
step "8/8 配置 systemd 服务"
# ══════════════════════════════════════════════════════════════════════════════

if [ "$SKIP_SYSTEMD" = true ]; then
  warn "已跳过 systemd 配置"
  info "手动启动: cd $INSTALL_DIR && .venv/bin/uvicorn main:app --host 0.0.0.0 --port $PORT"
else
  SERVICE_FILE="/etc/systemd/system/collei.service"
  info "生成 systemd 服务..."
  cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Collei Server Monitor
After=network.target

[Service]
Type=exec
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=${INSTALL_DIR}/.venv/bin/uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1
Restart=always
RestartSec=5

# 安全加固
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=${DATA_DIR}
ReadWritePaths=${INSTALL_DIR}
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable collei
  systemctl restart collei

  info "collei.service 已启用并启动"
fi

# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Collei 部署完成！${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  访问地址:   ${CYAN}http://<服务器IP>:${PORT}${NC}"
echo -e "  安装目录:   ${INSTALL_DIR}"
echo -e "  数据目录:   ${DATA_DIR}"
echo -e "  密钥文件:   ${DATA_DIR}/.secrets"
echo -e "  配置文件:   ${INSTALL_DIR}/.env"
echo ""
if [ "$SKIP_SYSTEMD" = false ]; then
  echo -e "  服务管理:"
  echo -e "    systemctl status collei    # 查看状态"
  echo -e "    systemctl restart collei   # 重启"
  echo -e "    journalctl -u collei -f    # 查看日志"
fi
echo ""
echo -e "  ${YELLOW}建议: 配置反向代理 (Nginx/Caddy) 启用 HTTPS${NC}"
echo -e "  ${YELLOW}      详见 README.md 中的反向代理配置示例${NC}"
echo ""

#!/usr/bin/env bash
# =============================================================================
# Collei 一键部署脚本
# 用法: bash deploy.sh [--reset-db] [--no-pull]
# =============================================================================
set -euo pipefail

# ── 颜色 ──────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ── 参数解析 ──────────────────────────────────────────────────────────────────
RESET_DB=false
NO_PULL=false
for arg in "$@"; do
  case $arg in
    --reset-db) RESET_DB=true ;;
    --no-pull)  NO_PULL=true  ;;
    -h|--help)
      echo "用法: $0 [--reset-db] [--no-pull]"
      echo "  --reset-db  删除现有数据库卷（⚠️ 数据将丢失）"
      echo "  --no-pull   跳过拉取最新镜像"
      exit 0
      ;;
    *) error "未知参数: $arg"; exit 1 ;;
  esac
done

# ── 依赖检查 ──────────────────────────────────────────────────────────────────
check_deps() {
  info "检查依赖..."
  local missing=()
  for cmd in docker curl; do
    if ! command -v "$cmd" &>/dev/null; then
      missing+=("$cmd")
    fi
  done
  # 检查 docker compose（v2 插件语法）
  if ! docker compose version &>/dev/null 2>&1; then
    missing+=("docker-compose（Docker Compose v2 插件）")
  fi
  if [[ ${#missing[@]} -gt 0 ]]; then
    error "缺少以下依赖: ${missing[*]}"
    error "请先安装后再运行此脚本。"
    exit 1
  fi
  success "依赖检查通过"
}

# ── .env 初始化 ───────────────────────────────────────────────────────────────
init_env() {
  if [[ ! -f .env ]]; then
    info "未找到 .env 文件，从 .env.example 生成..."
    cp .env.example .env

    # 自动生成安全随机密钥
    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(64))" 2>/dev/null \
      || openssl rand -base64 48 | tr -d '\n=')
    # 使用 sed 替换（兼容 macOS 和 Linux）
    if [[ "$(uname)" == "Darwin" ]]; then
      sed -i '' "s|COLLEI_SECRET_KEY=change-me-to-a-random-secret-key-in-production|COLLEI_SECRET_KEY=${SECRET_KEY}|" .env
    else
      sed -i "s|COLLEI_SECRET_KEY=change-me-to-a-random-secret-key-in-production|COLLEI_SECRET_KEY=${SECRET_KEY}|" .env
    fi

    success ".env 文件已创建，并自动生成了随机密钥"
    warn "⚠️  请编辑 .env 文件，设置 COLLEI_DEFAULT_ADMIN_PASSWORD 后再继续"
    warn "   管理员密码为空时将不会自动创建管理员账号"
    echo
    read -r -p "是否现在继续部署？(y/N): " CONTINUE
    [[ "${CONTINUE,,}" == "y" ]] || { info "部署已取消。"; exit 0; }
  else
    success "已找到 .env 文件"
  fi

  # 读取 .env
  # shellcheck disable=SC1091
  set -a; source .env; set +a
}

# ── 重置数据库（可选）────────────────────────────────────────────────────────
reset_db() {
  if [[ "$RESET_DB" == true ]]; then
    warn "⚠️  即将删除数据库卷 collei_collei-data，所有数据将丢失！"
    read -r -p "确认删除？(yes/N): " CONFIRM
    if [[ "${CONFIRM,,}" == "yes" ]]; then
      docker volume rm collei_collei-data 2>/dev/null || true
      success "数据库卷已删除"
    else
      info "已跳过重置数据库"
    fi
  fi
}

# ── 拉取/构建镜像 ─────────────────────────────────────────────────────────────
pull_and_build() {
  if [[ "$NO_PULL" == false ]]; then
    info "拉取最新镜像..."
    docker compose pull --ignore-pull-failures frontend || warn "前端镜像拉取失败（可能尚未发布），将跳过"
  fi

  info "构建后端镜像..."
  docker compose build backend
  success "镜像构建完成"
}

# ── 启动服务 ──────────────────────────────────────────────────────────────────
start_services() {
  info "启动 Collei 服务..."
  docker compose up -d

  # 等待后端健康检查
  info "等待后端服务就绪..."
  local max_wait=60
  local elapsed=0
  until docker compose exec -T backend python -c \
        "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" \
        &>/dev/null; do
    if [[ $elapsed -ge $max_wait ]]; then
      error "后端服务启动超时（${max_wait}s）"
      error "请运行 'docker compose logs backend' 查看日志"
      exit 1
    fi
    sleep 3
    elapsed=$((elapsed + 3))
    echo -n "."
  done
  echo

  success "所有服务已启动"
}

# ── 输出访问信息 ──────────────────────────────────────────────────────────────
print_info() {
  local PORT="${COLLEI_PORT:-80}"
  echo
  echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
  echo -e "${GREEN}║           🎉 Collei 部署成功！                          ║${NC}"
  echo -e "${GREEN}╠══════════════════════════════════════════════════════════╣${NC}"
  echo -e "${GREEN}║${NC}  访问地址:  http://localhost:${PORT}                        ${GREEN}║${NC}"
  echo -e "${GREEN}║${NC}  管理后台:  http://localhost:${PORT}                        ${GREEN}║${NC}"
  echo -e "${GREEN}╠══════════════════════════════════════════════════════════╣${NC}"
  echo -e "${GREEN}║${NC}  常用命令:                                             ${GREEN}║${NC}"
  echo -e "${GREEN}║${NC}    查看日志: docker compose logs -f                   ${GREEN}║${NC}"
  echo -e "${GREEN}║${NC}    停止服务: docker compose down                       ${GREEN}║${NC}"
  echo -e "${GREEN}║${NC}    重启服务: docker compose restart                   ${GREEN}║${NC}"
  echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
  echo
}

# ── 主流程 ────────────────────────────────────────────────────────────────────
main() {
  echo -e "${BLUE}"
  echo "  ╔═══════════════════════════════════════╗"
  echo "  ║       Collei 一键部署脚本             ║"
  echo "  ╚═══════════════════════════════════════╝"
  echo -e "${NC}"

  check_deps
  init_env
  reset_db
  pull_and_build
  start_services
  print_info
}

main "$@"

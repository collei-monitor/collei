# syntax=docker/dockerfile:1

# ══════════════════════════════════════════════════════════════════════════
#  Stage 1 — builder: 安装依赖 + 下载前端（不进入最终镜像）
# ══════════════════════════════════════════════════════════════════════════
FROM python:3.12-alpine AS builder

WORKDIR /build

# curl: 下载前端资源
RUN apk add --no-cache curl

# ── 创建虚拟环境 ──────────────────────────────────────────────────────────
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# ── 安装 Python 依赖（利用 Docker 层缓存）────────────────────────────────
COPY pyproject.toml .
RUN mkdir -p app && touch app/__init__.py && \
    pip install --no-cache-dir . && \
    pip uninstall -y collei && \
    rm -rf app

# ── 复制后端源码 + 安装本包（注册 CLI 入口点）────────────────────────────
COPY . .
RUN pip install --no-cache-dir --no-deps . && \
    rm -rf /build/build

# ── 下载预构建前端 ────────────────────────────────────────────────────────
ARG FRONTEND_VERSION=latest
RUN set -e; \
    if [ "$FRONTEND_VERSION" = "latest" ]; then \
      DOWNLOAD_URL=$(curl -fsS -o /dev/null -w '%{redirect_url}' \
        https://github.com/collei-monitor/collei-web/releases/latest); \
      FRONTEND_VERSION=$(basename "$DOWNLOAD_URL"); \
    fi; \
    echo ">>> Downloading frontend ${FRONTEND_VERSION}..."; \
    mkdir -p frontend/dist; \
    curl -fsSL -o /tmp/frontend.tar.gz \
      "https://github.com/collei-monitor/collei-web/releases/download/${FRONTEND_VERSION}/collei-web-${FRONTEND_VERSION}.tar.gz"; \
    tar xzf /tmp/frontend.tar.gz -C frontend/dist; \
    echo "${FRONTEND_VERSION}" > frontend/dist/.version; \
    rm /tmp/frontend.tar.gz

# ── 清理构建产物 ──────────────────────────────────────────────────────────
RUN pip uninstall -y pip setuptools 2>/dev/null; true && \
    pip cache purge 2>/dev/null; true && \
    find /opt/venv -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true

# ══════════════════════════════════════════════════════════════════════════
#  Stage 2 — runtime: 最小化生产镜像
# ══════════════════════════════════════════════════════════════════════════
FROM python:3.12-alpine

WORKDIR /app

# ── 环境变量 ──────────────────────────────────────────────────────────────
ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    SQLITE_TMPDIR=/tmp \
    PATH="/opt/venv/bin:$PATH"

# ── 运行时系统依赖 + 非 root 用户 ────────────────────────────────────────
#   su-exec: gosu 的 Alpine 轻量替代（~20KB vs ~2MB）
#   libstdc++: cryptography Rust FFI 运行时依赖
RUN apk add --no-cache su-exec libstdc++ && \
    addgroup -g 1000 collei && \
    adduser -u 1000 -G collei -s /sbin/nologin -D -H collei

# ── 从 builder 复制虚拟环境（包含所有 Python 依赖）───────────────────────
COPY --from=builder /opt/venv /opt/venv

# ── 从 builder 复制前端资源 ───────────────────────────────────────────────
COPY --from=builder /build/frontend/dist /app/frontend/dist

# ── 复制后端源码 ──────────────────────────────────────────────────────────
COPY . .

# ── 预编译字节码 + 创建数据目录 ───────────────────────────────────────────
RUN python -m compileall -q . && \
    mkdir -p /data && \
    chmod +x /app/entrypoint.sh

EXPOSE 22333

ENTRYPOINT ["/app/entrypoint.sh"]

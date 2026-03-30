# syntax=docker/dockerfile:1

FROM python:3.12-slim

WORKDIR /app

# ── 环境变量 ──────────────────────────────────────────────────────────────
ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    SQLITE_TMPDIR=/tmp

# ── 系统依赖 + 非 root 用户 ──────────────────────────────────────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends gosu curl && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd -g 1000 collei && \
    useradd -u 1000 -g collei -s /sbin/nologin -M collei

# ── 安装 Python 依赖（利用 Docker 层缓存）────────────────────────────────
COPY pyproject.toml .
RUN mkdir -p app && touch app/__init__.py && \
    pip install --no-cache-dir . && \
    pip uninstall -y collei && \
    rm -rf app

# ── 复制后端源码 ──────────────────────────────────────────────────────────
COPY . .

# ── 下载预构建前端 ────────────────────────────────────────────────────────
ARG FRONTEND_VERSION=latest
RUN set -e; \
    if [ "$FRONTEND_VERSION" = "latest" ]; then \
      DOWNLOAD_URL=$(curl -fsSL -o /dev/null -w '%{redirect_url}' \
        https://github.com/collei-monitor/collei-web/releases/latest); \
      FRONTEND_VERSION=$(basename "$DOWNLOAD_URL"); \
    fi; \
    echo ">>> Downloading frontend ${FRONTEND_VERSION}..."; \
    mkdir -p frontend/dist && \
    curl -fsSL "https://github.com/collei-monitor/collei-web/releases/download/${FRONTEND_VERSION}/collei-web-${FRONTEND_VERSION}.tar.gz" \
      | tar xz -C frontend/dist

# ── 预编译字节码 + 创建数据目录 ────────────────────────────────
RUN python -m compileall -q . && \
    mkdir -p /data && \
    chmod +x /app/entrypoint.sh

EXPOSE 22333

ENTRYPOINT ["/app/entrypoint.sh"]

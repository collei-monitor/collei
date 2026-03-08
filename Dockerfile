# syntax=docker/dockerfile:1

# ─── Stage 1: 克隆并构建前端 ─────────────────────────────────────────────────
FROM node:22-alpine AS frontend-builder

# 通过 build arg 传入前端仓库地址，也可在 docker-compose.yml 中覆盖
ARG FRONTEND_REPO=https://github.com/YOUR_USERNAME/collei-web.git
ARG FRONTEND_REF=main

RUN apk add --no-cache git

RUN git clone --depth 1 --branch ${FRONTEND_REF} ${FRONTEND_REPO} /build

WORKDIR /build
RUN npm install
# 使用 .env.production（VITE_API_BASE_URL=/api/v1）
RUN npm run build

# ─── Stage 2: Python 运行时 ───────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# 先单独安装依赖，充分利用 Docker 层缓存
COPY pyproject.toml .
RUN mkdir -p app && touch app/__init__.py && \
    pip install --no-cache-dir . && \
    rm -rf app/__init__.py

# 复制后端全部源码
COPY . .

# 复制前端构建产物到 main.py 期望的位置（frontend/dist）
COPY --from=frontend-builder /build/dist ./frontend/dist

# 持久化数据卷（SQLite 数据库）
VOLUME ["/data"]

EXPOSE 8000

RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]

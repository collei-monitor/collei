# ── 构建阶段 ──────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖文件，利用层缓存
COPY pyproject.toml .
# 创建最小包结构以安装依赖
RUN mkdir -p app && touch app/__init__.py

# 仅安装运行时依赖（不含当前包）到独立目录
RUN pip install --no-cache-dir --prefix=/install \
    "fastapi[standard]>=0.115" \
    "uvicorn[standard]>=0.34" \
    "sqlalchemy[asyncio]>=2.0" \
    "aiosqlite>=0.21" \
    "alembic>=1.15" \
    "pydantic>=2.0" \
    "pydantic-settings>=2.0" \
    "python-jose[cryptography]>=3.3" \
    "passlib[bcrypt]>=1.7" \
    "bcrypt>=4.0,<4.1" \
    "python-multipart>=0.0.20" \
    "httpx>=0.28" \
    "pyotp>=2.9" \
    "maxminddb>=2.0"

# ── 运行阶段 ──────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# 从构建阶段复制依赖
COPY --from=builder /install /usr/local

# 复制应用代码
COPY alembic.ini .
COPY alembic/ alembic/
COPY app/ app/
COPY main.py .
COPY data/ data/
COPY docker/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

# 暴露端口
EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]

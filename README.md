# Collei

一个基于 **FastAPI + React** 的自托管服务器监控工具。

- **后端**：本仓库（`collei-monitor/collei`），FastAPI + SQLite
- **前端**：[collei-monitor/collei-web](https://github.com/collei-monitor/collei-web)，React + Vite

---

## 一键部署

### 前置要求

- [Docker](https://docs.docker.com/get-docker/) ≥ 24.0
- [Docker Compose](https://docs.docker.com/compose/) v2（通常随 Docker Desktop 安装）

### 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/collei-monitor/collei.git
cd collei

# 2. 一键部署
bash deploy.sh
```

脚本会自动：
1. 检查依赖
2. 从 `.env.example` 生成 `.env` 并自动生成随机密钥
3. 构建后端 Docker 镜像
4. 拉取前端镜像（`ghcr.io/collei-monitor/collei-web:latest`）
5. 启动全部服务（后端 + 前端 + Nginx 反向代理）

默认访问地址：**http://localhost**

---

## 手动部署

如果需要更细粒度的控制，可以手动操作：

```bash
# 复制并编辑环境变量
cp .env.example .env
# 编辑 .env，至少设置以下字段：
#   COLLEI_SECRET_KEY=<随机强密钥>
#   COLLEI_DEFAULT_ADMIN_PASSWORD=<初始管理员密码>

# 构建并启动
docker compose up -d --build

# 查看日志
docker compose logs -f
```

---

## 配置说明

所有配置均通过环境变量（或 `.env` 文件）传入，以 `COLLEI_` 为前缀。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `COLLEI_DEBUG` | `false` | 调试模式（开启 `/docs`） |
| `COLLEI_SECRET_KEY` | — | JWT 签名密钥（**生产环境必须修改**） |
| `COLLEI_DATABASE_URL` | SQLite | 数据库连接串 |
| `COLLEI_ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | Token 有效期（分钟） |
| `COLLEI_DEFAULT_ADMIN_USERNAME` | `admin` | 初始管理员用户名 |
| `COLLEI_DEFAULT_ADMIN_PASSWORD` | — | 初始管理员密码（留空则不自动创建） |
| `COLLEI_PORT` | `80` | Nginx 监听端口 |

完整说明见 [.env.example](.env.example)。

---

## 服务架构

```
                    ┌──────────────────────────────────┐
用户浏览器  ──────►  │  Nginx（:80）反向代理            │
                    │                                   │
                    │  /api/*  ──► backend:8000         │
                    │  /       ──► frontend:80          │
                    └──────────────────────────────────┘
                             │                │
                    ┌────────▼──────┐  ┌──────▼──────────┐
                    │ FastAPI 后端  │  │  React 前端      │
                    │ (Python 3.11) │  │  (Nginx 静态)    │
                    └───────────────┘  └─────────────────┘
                             │
                    ┌────────▼──────┐
                    │  SQLite DB    │
                    │ (持久卷挂载)   │
                    └───────────────┘
```

---

## 常用命令

```bash
# 查看所有服务日志
docker compose logs -f

# 只看后端日志
docker compose logs -f backend

# 停止服务
docker compose down

# 重启服务
docker compose restart

# 更新到最新版本
git pull
bash deploy.sh --no-pull  # 跳过前端镜像拉取（仅重新构建后端）
# 或
docker compose pull && docker compose up -d --build

# 重置数据库（⚠️ 数据将丢失）
bash deploy.sh --reset-db
```

---

## 数据持久化

数据库文件存储在 Docker volume `collei_collei-data` 中。

```bash
# 备份数据库
docker run --rm \
  -v collei_collei-data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/collei-backup.tar.gz -C /data .

# 恢复数据库
docker run --rm \
  -v collei_collei-data:/data \
  -v $(pwd):/backup \
  alpine tar xzf /backup/collei-backup.tar.gz -C /data
```

---

## 开发环境

```bash
# 安装依赖
pip install -e ".[dev]"

# 初始化数据库
alembic upgrade head

# 启动开发服务器
uvicorn main:app --reload

# API 文档（调试模式下）
# http://localhost:8000/docs
```

---

## API 端点

| 前缀 | 描述 |
|---|---|
| `GET /api/v1/health` | 健康检查（无需认证） |
| `POST /api/v1/auth/login` | 用户登录 |
| `GET /api/v1/clients/servers` | 服务器列表 |
| `GET /api/v1/clients/public/servers` | 公开服务器列表（无需认证） |
| `WS /api/v1/ws` | 实时数据 WebSocket |

完整文档：启动后访问 `http://localhost/api/v1/docs`（需开启 `COLLEI_DEBUG=true`）。

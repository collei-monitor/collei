# Collei

一个多功能的自托管服务器监控工具。

## 功能特性

- **实时监控** — CPU、内存、磁盘、网络 I/O，通过 WebSocket 实时推送
- **多节点管理** — 集中管理所有服务器，支持分组、排序、标签
- **告警通知** — 灵活的告警规则，支持 Telegram、Email、Webhook 等多通道
- **网络探测** — 分布式 ICMP/TCP/HTTP 探测，多节点协同
- **Web SSH** — 浏览器内 SSH 终端 + SFTP 文件管理器
- **SSH CA** — 内置 SSH 证书颁发，免密码登录
- **DNS 管理** — 域名 DNS 记录管理 + DDNS 动态更新
- **SSO 登录** — 支持 GitHub、Google、自定义 OIDC 提供商
- **双因素认证** — TOTP 2FA 保护管理员账户
- **自定义主题** — 展示页支持自定义 HTML/CSS/JS 主题
- **国际化** — 中文 / English 双语界面

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.12 · FastAPI · SQLAlchemy (async) · SQLite |
| 前端 | React · TypeScript · TanStack Query · Tailwind CSS |
| Agent | collei-agent (Go) |
| 部署 | Docker · systemd · UV |

## 快速部署

### 方式一：Docker（推荐）

```bash
# 创建目录并下载 compose 文件
mkdir collei && cd collei
curl -fsSL https://raw.githubusercontent.com/collei-monitor/collei/master/docker-compose.yml -o docker-compose.yml

# 一键启动
docker compose up -d
```

访问 `http://<服务器IP>:22333`。首次启动自动创建管理员账号，查看日志获取密码：

```bash
docker compose logs collei | grep "密码"
```

> **密钥管理**：`SECRET_KEY` 与 `CA_MASTER_KEY` 首次启动时自动生成并持久化到 `/data/.secrets`。重启不会丢失。如需手动指定，编辑 `.env` 文件设置对应变量。

#### 自定义配置

所有自定义通过 `.env` 文件完成，无需修改 `docker-compose.yml`：

```bash
# 创建 .env 文件（与 docker-compose.yml 同目录）
cat > .env <<EOF
# 指定镜像版本（默认 latest）
# COLLEI_VERSION=0.1.0

# 修改端口映射（默认 22333）
# COLLEI_PORT=8080

# 应用配置
# COLLEI_DEBUG=false
# COLLEI_DEFAULT_ADMIN_PASSWORD=your-password
EOF
```

#### 指定版本

```bash
# 通过 .env 固定版本
echo "COLLEI_VERSION=0.1.0" >> .env
docker compose up -d
```

### 方式二：裸机部署（UV 一键脚本）

> 适用于使用 systemd 的发行版（Ubuntu、Debian 等），脚本自动安装 [UV](https://docs.astral.sh/uv/) 作为 Python 包管理器。

```bash
curl -fsSL https://raw.githubusercontent.com/collei-monitor/collei/master/deploy.sh -o deploy.sh
sudo bash deploy.sh
```

#### 自定义参数

```bash
sudo bash deploy.sh \
  --install-dir /opt/collei \
  --data-dir /var/lib/collei \
  --port 22333 \
  --frontend-version v0.0.1
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--install-dir` | `/opt/collei` | 应用安装目录 |
| `--data-dir` | `/var/lib/collei` | 数据持久化目录（数据库、GeoIP、密钥） |
| `--port` | `22333` | HTTP 监听端口 |
| `--frontend-version` | `latest` | 前端版本号（如 `v0.0.1`） |
| `--skip-systemd` | — | 跳过 systemd 服务配置 |

部署完成后，脚本会显示访问地址和管理员密码。

## 环境变量

所有环境变量以 `COLLEI_` 为前缀，可通过 `.env` 文件或系统环境变量配置。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `COLLEI_SECRET_KEY` | *自动生成* | JWT 签名密钥。留空则自动生成并持久化 |
| `COLLEI_CA_MASTER_KEY` | *自动生成* | SSH CA 加密主密钥。留空则自动生成并持久化 |
| `COLLEI_DATABASE_URL` | `sqlite+aiosqlite:///collei.db` | 数据库连接字符串 |
| `COLLEI_DATA_DIR` | `./data` | 数据目录（GeoIP / 主题 / CA 密钥） |
| `COLLEI_DEBUG` | `false` | 调试模式（启用 `/docs` API 文档） |
| `COLLEI_DEFAULT_ADMIN_USERNAME` | `admin` | 初始管理员用户名 |
| `COLLEI_DEFAULT_ADMIN_PASSWORD` | *自动生成* | 初始管理员密码，留空则随机生成（见日志） |
| `COLLEI_TRUSTED_PROXIES` | `*` | 可信反向代理 IP，逗号分隔；`*` 信任所有 |
| `COLLEI_COOKIE_SECURE` | `true` | Cookie 仅通过 HTTPS 发送（本地开发设为 `false`） |
| `COLLEI_COOKIE_SAMESITE` | `lax` | Cookie SameSite 策略 |
| `COLLEI_ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | Token 有效期（分钟） |
| `COLLEI_SESSION_EXPIRE_DAYS` | `7` | 会话有效期（天） |
| `COLLEI_LOGIN_ATTEMPT_LIMIT` | `10` | 登录失败次数上限 |
| `COLLEI_LOGIN_ATTEMPT_WINDOW` | `600` | 登录失败统计窗口（秒） |

完整配置示例见 [`.env.example`](.env.example)。

## 反向代理配置

Collei 监听 HTTP `22333` 端口，生产环境强烈建议使用反向代理提供 HTTPS。

### Nginx

```nginx
upstream collei {
    server 127.0.0.1:22333;
}

server {
    listen 443 ssl http2;
    server_name monitor.example.com;

    ssl_certificate     /etc/letsencrypt/live/monitor.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/monitor.example.com/privkey.pem;

    # WebSocket（SSH 终端、实时监控）
    location ~ ^/(api/v1/ws|ws)/ {
        proxy_pass http://collei;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400s;
    }

    # API + 前端
    location / {
        proxy_pass http://collei;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name monitor.example.com;
    return 301 https://$host$request_uri;
}
```

### Caddy

```
monitor.example.com {
    reverse_proxy localhost:22333
}
```

> Caddy 自动处理 HTTPS 证书和 WebSocket 升级，无需额外配置。

配置反向代理后，设置环境变量：

```bash
# 默认已信任所有代理（COLLEI_TRUSTED_PROXIES=*），通常无需修改
COLLEI_COOKIE_SECURE=true
```

## 升级

### Docker 升级

```bash
cd collei

# 拉取最新镜像并重启（数据保留在 volume 中）
docker compose pull
docker compose up -d
```

升级到指定版本：

```bash
# 编辑 .env，设置目标版本
echo "COLLEI_VERSION=0.2.0" > .env
docker compose up -d
```

### 裸机升级

```bash
curl -fsSL https://raw.githubusercontent.com/collei-monitor/collei/master/upgrade.sh -o upgrade.sh
sudo bash upgrade.sh
```

脚本自动执行：备份数据库 → `git pull` → 更新依赖 → 下载最新前端 → 数据库迁移 → 重启服务。

```bash
# 自定义参数
sudo bash upgrade.sh \
  --install-dir /opt/collei \
  --data-dir /var/lib/collei \
  --frontend-version v0.0.2
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--install-dir` | `/opt/collei` | 安装目录 |
| `--data-dir` | `/var/lib/collei` | 数据目录 |
| `--frontend-version` | `latest` | 前端版本号 |
| `--skip-restart` | — | 跳过服务重启 |

## Docker 安全特性

生产环境默认启用以下安全加固：

| 特性 | 说明 |
|------|------|
| 非 root 运行 | 应用以 `collei` 用户 (uid=1000) 运行 |
| `read_only: true` | 容器文件系统只读，仅 `/data` 卷和 `/tmp` 可写 |
| `cap_drop: ALL` | 丢弃所有 Linux capabilities |
| `cap_add: CHOWN, SETUID, SETGID` | 仅保留 chown + su-exec 降权所需最小权限 |
| `no-new-privileges` | 阻止 suid/sgid 提权 |
| 密钥自动持久化 | `SECRET_KEY` / `CA_MASTER_KEY` 保存在 `/data/.secrets`（chmod 600） |

### 开发者本地构建

如果你需要修改源码并本地构建镜像，使用 `docker-compose.build.yml`：

```bash
git clone https://github.com/collei-monitor/collei.git
cd collei

# 本地构建并启动
docker compose -f docker-compose.build.yml up -d --build

# 指定前端版本
FRONTEND_VERSION=v0.1.0 docker compose -f docker-compose.build.yml up -d --build
```

## 项目结构

```
collei/
├── main.py              # FastAPI 应用入口
├── app/
│   ├── api/v1/          # REST API 路由
│   ├── core/            # 核心模块（配置、安全、告警引擎、SSH 等）
│   ├── crud/            # 数据库 CRUD 操作
│   ├── db/              # SQLAlchemy 模型与会话
│   ├── models/          # ORM 模型定义
│   └── schemas/         # Pydantic 请求/响应模型
├── alembic/             # 数据库迁移脚本
├── data/                # GeoIP 数据库
├── frontend/dist/       # 前端构建产物（部署时下载）
├── deploy.sh            # 裸机一键部署脚本
├── upgrade.sh           # 裸机升级脚本
├── Dockerfile           # Docker 镜像定义
├── docker-compose.yml   # Docker Compose 编排（用户部署用）
├── docker-compose.build.yml # Docker 本地构建（开发者用）
└── entrypoint.sh        # 容器入口脚本
```

## License

MIT

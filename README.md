# Collei

一个多功能的自托管服务器监控工具。
[文档](https://collei-monitor.github.io/collei-document/)

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

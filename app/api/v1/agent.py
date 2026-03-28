"""Agent 端 API 路由（无需面板登录认证）.

端点:
  POST  /agent/register        Agent 自动注册（全局密钥）
  POST  /agent/verify           Agent 验证 token（被动注册）
  POST  /agent/report           Agent 混合上报（硬件 + 监控数据）
  POST  /agent/tasks/report     Agent 上报任务执行结果
  GET   /agent/download         代理下载 Agent 二进制（流式转发，不落盘）
  GET   /agent/install-script   代理下载安装脚本（流式转发，不落盘）
"""

from __future__ import annotations

import ipaddress
import json
import logging
import time
import urllib.parse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config_cache import config_cache
from app.core.geoip import DEFAULT_DB, lookup_region, remap_region
from app.core.audit import audit
from app.core.server_cache import server_cache
from app.crud import clients as crud_clients
from app.crud import monitoring as crud_monitoring
from app.crud import network as crud_network
from app.crud import task as crud_task
from app.db.session import get_async_session
from app.schemas.agent import (
    AgentRegisterRequest,
    AgentRegisterResponse,
    AgentReportRequest,
    AgentReportResponse,
    AgentVerifyRequest,
    AgentVerifyResponse,
)
from app.schemas.task import (
    AgentPendingTask,
    AgentTaskReport,
    MessageResponse,
    TaskExecutionRead,
)

router = APIRouter(prefix="/agent", tags=["agent"])

_log = logging.getLogger(__name__)

_DOWNLOAD_CHUNK_SIZE = 65_536  # 64 KB
_DEFAULT_MAX_SIZE = 200 * 1024 * 1024  # 200 MB
_SCRIPT_MAX_SIZE = 1 * 1024 * 1024  # 1 MB — 安装脚本体积上限
_UPSTREAM_CONNECT_TIMEOUT = 15  # 秒
_UPSTREAM_READ_TIMEOUT = 300  # 秒


# ─── 辅助函数 ─────────────────────────────────────────────────────────────────

async def _get_config_value(db: AsyncSession, key: str) -> str | None:
    """从 configs 表获取配置值."""
    from app.crud import config as crud_config
    return await crud_config.get_config_value(db, key)

async def _resolve_region(ipv4: str | None, ipv6: str | None, db: AsyncSession) -> str | None:
    """优先用 IPv4，否则用 IPv6 查询归属国家/地区代码."""
    from app.crud import config as crud_config
    db_name = await crud_config.get_config_value(db, "ip_db") or DEFAULT_DB
    ip = ipv4 or ipv6
    code = await lookup_region(ip, db_name)
    disputed = config_cache.get("disputed_territory") == "1"
    return remap_region(code, disputed)

# ═══════════════════════════════════════════════════════════════════════════════
# Agent 注册 & 验证
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/register", response_model=AgentRegisterResponse)
async def agent_register(
    body: AgentRegisterRequest,
    db: AsyncSession = Depends(get_async_session),
):
    """Agent 自动注册 — 携带全局安装密钥 + 硬件信息.

    流程:
      1. 从 configs 表读取全局注册密钥进行校验
      2. 创建新服务器记录（is_approved=0，需管理员审核）
      3. 返回专属 uuid + token 供 Agent 后续通信
    """
    # 校验全局安装密钥
    global_token = await _get_config_value(db, "global_registration_token")
    if not global_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Registration is not configured",
        )
    if body.reg_token != global_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid registration token",
        )

    # 收集硬件信息
    hardware = body.model_dump(exclude={"reg_token", "name"})

    # 自动解析 IP 归属国家/地区
    region = await _resolve_region(body.ipv4, body.ipv6, db)
    if region:
        hardware["region"] = region

    server = await crud_clients.create_server(
        db,
        name=body.name,
        is_approved=0,  # 自动注册需审核
        hardware_info=hardware,
    )
    await audit.emit(
        db, msg_type="server", message="Agent 自动注册",
        detail=f"name={body.name}, uuid={server.uuid}",
        source="agent", server_uuid=server.uuid,
    )

    return AgentRegisterResponse(
        uuid=server.uuid,
        token=server.token,  # type: ignore[arg-type]
    )


@router.post("/verify", response_model=AgentVerifyResponse)
async def agent_verify(
    body: AgentVerifyRequest,
    db: AsyncSession = Depends(get_async_session),
):
    """Agent 被动注册验证使用管理员下发的 token
    或已注册服务器的 token 验证（Agent重启时验证）.

    流程:
      1. 根据 token 查找服务器记录
      2. 更新 Agent 上报的硬件信息
      3. 返回 uuid + token + is_approved 状态
    """
    server = await crud_clients.get_server_by_token(db, body.token)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    # 更新硬件信息
    hardware = body.model_dump(exclude={"token"})

    # 自动解析 IP 归属国家/地区
    region = await _resolve_region(body.ipv4, body.ipv6, db)
    if region and server.is_region_locked != 1:
        hardware["region"] = region

    await crud_clients.update_server_hardware(db, server.uuid, hardware)

    # 确保存在 server_status 记录
    await crud_clients.upsert_server_status(db, server.uuid)

    # 同步内存缓存（is_approved==1 时写入，0 时 update_server 内部直接返回）
    server_snap: dict = {f: getattr(server, f, None) for f in (
        "name", "top", "cpu_name", "cpu_cores", "arch", "os",
        "region", "mem_total", "swap_total", "disk_total", "virtualization",
        "hidden", "is_approved", "created_at", "token", "enable_statistics_mode",
    )}
    server_snap.update(hardware)  # 覆盖已更新的硬件字段（含解析后的 region）
    server_cache.update_server(server.uuid, server_snap)

    # ── 网络监控：对已批准节点下发最新探测任务 ──
    network_dispatch_resp: dict | None = None
    if server.is_approved == 1:
        targets = await crud_network.get_dispatch_targets_for_server(db, server.uuid)
        current_version = crud_network.compute_dispatch_hash(targets)
        network_dispatch_resp = {
            "version": current_version,
            "targets": [
                {
                    "id": t.id,
                    "name": t.name,
                    "host": t.host,
                    "protocol": t.protocol,
                    "port": t.port,
                    "interval": t.interval,
                }
                for t in targets
            ],
        }

    return AgentVerifyResponse(
        uuid=server.uuid,
        token=server.token,  # type: ignore[arg-type]
        is_approved=server.is_approved,
        network_dispatch=network_dispatch_resp,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 混合上报（硬件 + 监控数据）
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/report", response_model=AgentReportResponse)
async def agent_report(
    body: AgentReportRequest,
    db: AsyncSession = Depends(get_async_session),
):
    """Agent 混合上报 — 同时更新硬件信息并写入监控数据.

    与 /agent/verify 不同：
      - verify 仅在首次连接时验证身份并上报硬件信息
      - report 用于持续上报，可同时携带硬件变更和实时资源数据

    流程:
      1. 根据 token 认证 Agent 身份
      2. 检查服务器是否已批准
      3. 如果携带硬件信息，更新 servers 表
      4. 如果携带监控数据，写入 load_now 表
      5. 更新 server_status（在线时间、状态）
    """
    # 优先从内存缓存查 token（缓存仅含 is_approved==1 的服务器）
    cached_uuid = server_cache.get_uuid_by_token(body.token)
    cached_info: dict | None = None
    if cached_uuid:
        cached_info = server_cache._servers[cached_uuid]

        class _CachedServer:
            uuid = cached_uuid
            is_approved: int = 1  # 只有 is_approved==1 的服务器才在缓存中
            enable_statistics_mode: int = cached_info.get("enable_statistics_mode", 0)  # type: ignore[assignment]

        server = _CachedServer()  # type: ignore[assignment]
    else:
        # 缓存未命中（启动预热前或 token 不合法）→ 回落数据库
        server = await crud_clients.get_server_by_token(db, body.token)
        if not server:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )
        if server.is_approved != 1:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Server not approved",
            )

    now = int(time.time())

    # ── 更新硬件信息（如果有变更） ──
    hardware_fields = body.model_dump(
        include={
            "name", "cpu_name", "virtualization", "arch", "cpu_cores",
            "os", "kernel_version", "ipv4", "ipv6",
            "mem_total", "swap_total", "disk_total", "version",
        },
        exclude_none=True,
    )

    # 当上报中包含 IP 时重新解析 region
    if body.ipv4 is not None or body.ipv6 is not None:
        # 仅在未锁定时更新 region
        is_locked = getattr(server, "is_region_locked", None)
        if is_locked is None:
            # 缓存对象没有该属性，查数据库
            db_server = await crud_clients.get_server_by_uuid(db, server.uuid)
            is_locked = db_server.is_region_locked if db_server else 0
        if is_locked != 1:
            region = await _resolve_region(body.ipv4, body.ipv6, db)
            if region:
                hardware_fields["region"] = region

    # ── 检测 Agent 版本变更 ──
    if body.version is not None:
        old_version = (
            cached_info.get("version") if cached_uuid
            else getattr(server, "version", None)
        )
        if old_version and old_version != body.version:
            await audit.emit(
                db, msg_type="server",
                message="Agent 版本变更",
                detail=f"{old_version} → {body.version}",
                source="agent",
                server_uuid=server.uuid,
            )

    if hardware_fields:
        await crud_clients.update_server_hardware(
            db, server.uuid, hardware_fields,
        )

    # ── 写入监控数据（如果有） ──
    load_dict: dict = {}
    if body.load_data:
        load_dict = body.load_data.model_dump(exclude_none=True)
        if load_dict:
            await crud_monitoring.insert_load(
                db,
                server_uuid=server.uuid,
                data=load_dict,
                ts=now,
            )

            # ── 流量统计模式：累加到 traffic_hourly_stats ──
            if server.enable_statistics_mode == 1:
                net_in = load_dict.get("net_in")
                net_out = load_dict.get("net_out")
                if net_in is not None or net_out is not None:
                    await crud_monitoring.upsert_traffic_hourly(
                        db,
                        server_uuid=server.uuid,
                        net_in=net_in or 0,
                        net_out=net_out or 0,
                        ts=now,
                    )
                    # 增量累加缓存中的周期流量
                    server_cache.add_cycle_traffic(
                        server.uuid, net_in or 0, net_out or 0,
                    )

    # ── 更新服务器状态为在线 ──
    status_kwargs: dict = dict(
        status_val=1,
        last_online=now,
        boot_time=body.boot_time,
    )
    if body.total_flow_out is not None:
        status_kwargs["total_flow_out"] = body.total_flow_out
    if body.total_flow_in is not None:
        status_kwargs["total_flow_in"] = body.total_flow_in
    if body.current_disk_io is not None:
        status_kwargs["current_disk_io"] = json.dumps(
            body.current_disk_io, ensure_ascii=False)
    if body.current_net_io is not None:
        status_kwargs["current_net_io"] = json.dumps(
            body.current_net_io, ensure_ascii=False)

    await crud_clients.upsert_server_status(
        db,
        server.uuid,
        **status_kwargs,
    )

    # ── 同步内存缓存 ──
    if hardware_fields:
        server_cache.update_server(server.uuid, hardware_fields)
    cache_status_kwargs: dict = dict(status=1, last_online=now, boot_time=body.boot_time)
    if body.total_flow_out is not None:
        cache_status_kwargs["total_flow_out"] = body.total_flow_out
    if body.total_flow_in is not None:
        cache_status_kwargs["total_flow_in"] = body.total_flow_in
    if body.current_disk_io is not None:
        cache_status_kwargs["current_disk_io"] = json.dumps(
            body.current_disk_io, ensure_ascii=False)
    if body.current_net_io is not None:
        cache_status_kwargs["current_net_io"] = json.dumps(
            body.current_net_io, ensure_ascii=False)
    server_cache.update_status(server.uuid, **cache_status_kwargs)
    if load_dict:
        server_cache.update_load(server.uuid, load_dict)

    # ── 网络监控：写入探测结果 + 增量下发 ──
    network_dispatch_resp: dict | None = None

    # 写入 Agent 上报的探测结果
    if body.network_data:
        await crud_network.batch_insert_network_status(
            db, body.network_data, server_uuid=server.uuid,
        )

    # 增量下发：对比该节点目标版本决定是否返回更新的目标列表
    targets = await crud_network.get_dispatch_targets_for_server(db, server.uuid)
    current_version = crud_network.compute_dispatch_hash(targets)

    if body.network_version != current_version:
        network_dispatch_resp = {
            "version": current_version,
            "targets": [
                {
                    "id": t.id,
                    "name": t.name,
                    "host": t.host,
                    "protocol": t.protocol,
                    "port": t.port,
                    "interval": t.interval,
                }
                for t in targets
            ],
        }
    else:
        network_dispatch_resp = {"version": current_version, "targets": None}

    # ── SSH 隧道：按需通知 Agent 建立 / 断开隧道 WS ──
    from app.core.ssh_manager import ssh_manager
    ssh_tunnel_resp = ssh_manager.get_ssh_tunnel_response(server.uuid)

    # ── 待执行任务：查询并下发给 Agent ──
    pending_tasks_resp: list[dict] | None = None
    pending_execs = await crud_task.get_pending_executions_for_agent(db, server.uuid)
    if pending_execs:
        pending_tasks_resp = []
        for ex in pending_execs:
            await crud_task.mark_execution_dispatched(db, ex.id)
            pending_tasks_resp.append({
                "execution_id": ex.id,
                "task_id": ex.task_id,
                "type": ex.task.type,
                "payload": ex.task.payload,
                "timeout_sec": ex.task.timeout_sec,
            })

    return AgentReportResponse(
        uuid=server.uuid,
        is_approved=server.is_approved,
        network_dispatch=network_dispatch_resp,
        ssh_tunnel=ssh_tunnel_resp,
        pending_tasks=pending_tasks_resp,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 任务执行结果上报
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/tasks/report", response_model=MessageResponse)
async def agent_task_report(
    body: AgentTaskReport,
    db: AsyncSession = Depends(get_async_session),
):
    """Agent 上报任务执行结果.

    流程:
      1. 根据 execution_id 查找执行记录
      2. 校验状态流转合法性（sent/running → running/success/failed/timeout）
      3. 更新执行状态、退出码、完成时间
      4. 写入 / 追加终端输出日志
    """
    execution = await crud_task.get_execution(db, body.execution_id)
    if not execution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Execution not found",
        )

    # 校验状态流转
    allowed_transitions = {
        "sent": {"running", "success", "failed", "timeout"},
        "running": {"running", "success", "failed", "timeout"},
    }
    allowed = allowed_transitions.get(execution.status, set())
    if body.status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot transition from '{execution.status}' to '{body.status}'",
        )

    now = int(time.time())
    completed_at = now if body.status in ("success", "failed", "timeout") else None

    await crud_task.update_execution_status(
        db,
        body.execution_id,
        status=body.status,
        exit_code=body.exit_code,
        completed_at=completed_at,
    )

    if body.output is not None:
        await crud_task.upsert_execution_log(db, body.execution_id, body.output)

    return MessageResponse(message="Task result received")


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 二进制代理下载
# ═══════════════════════════════════════════════════════════════════════════════

# SSRF 防护：拒绝将请求代理到私有 / 保留地址
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_private_host(hostname: str) -> bool:
    """检查主机名解析后是否为私有 / 保留 IP（防止 SSRF）."""
    import socket

    try:
        # 解析为 IP（支持 IPv4 和 IPv6）
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        return True  # 无法解析时拒绝
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        for net in _PRIVATE_NETWORKS:
            if addr in net:
                return True
    return False


def _validate_agent_url(url: str, config_name: str = "url") -> None:
    """校验 URL 是否为合法的外部 HTTP(S) URL.

    Raises:
        HTTPException: URL 为空 / 非法 / 指向内网时抛出。
    """
    if not url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{config_name} is not configured",
        )
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{config_name} must use http or https scheme",
        )
    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{config_name} has no valid hostname",
        )
    if _is_private_host(hostname):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{config_name} must not point to a private/reserved address",
        )


@router.get("/download")
async def agent_download(
    token: str = Query(..., min_length=1, description="reg-token 或 server token"),
    url: str = Query(..., min_length=1, description="上游文件完整 URL（由安装脚本传入）"),
    db: AsyncSession = Depends(get_async_session),
):
    """代理下载 Agent 二进制 — 流式转发，不在面板本地落盘.

    面板作为纯透明代理：接受安装脚本传入的完整下载 URL，进行 SSRF 校验后
    流式转发上游响应给客户端。

    鉴权:
      - token 匹配 global_registration_token（自动注册模式），或
      - token 匹配某台 server 的专属 token（被动注册模式）

    流程:
      1. 校验 token
      2. 对传入的 url 做 SSRF 安全检查
      3. 发起上游 HTTP 请求，以流式响应逐 chunk 转发给客户端
    """
    # ── 鉴权 ──
    valid = False
    global_token = config_cache.get("global_registration_token", "")
    if global_token and token == global_token:
        valid = True
    if not valid:
        # 检查缓存中的 server token（仅 approved 服务器）
        if server_cache.get_uuid_by_token(token):
            valid = True
    if not valid:
        # 回落数据库查询（含未批准服务器）
        srv = await crud_clients.get_server_by_token(db, token)
        if srv:
            valid = True
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    # ── 校验传入的 URL ──
    _log.info("agent download: proxying url=%s", url)
    _validate_agent_url(url)

    # ── 最大下载体积 ──
    max_size_str = config_cache.get("agent_download_max_size", "")
    try:
        max_size = int(max_size_str) if max_size_str else _DEFAULT_MAX_SIZE
    except (ValueError, TypeError):
        max_size = _DEFAULT_MAX_SIZE

    # ── 流式代理 ──
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=_UPSTREAM_CONNECT_TIMEOUT,
            read=_UPSTREAM_READ_TIMEOUT,
            write=10,
            pool=10,
        ),
        follow_redirects=True,
        headers={"User-Agent": "Collei-Panel/1.0"},
    )

    try:
        req = client.build_request("GET", url)
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        _log.warning("agent download: upstream request failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to connect to upstream",
        )

    if upstream.status_code != 200:
        body_text = (await upstream.aread()).decode(errors="replace")[:200]
        await upstream.aclose()
        await client.aclose()
        _log.warning("agent download: upstream returned %s: %s", upstream.status_code, body_text)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Upstream returned HTTP {upstream.status_code}",
        )

    # Content-Type 防护：拒绝 HTML 响应（常见误配置导致拉到面板首页）
    upstream_ct = upstream.headers.get("content-type", "")
    if "text/html" in upstream_ct:
        await upstream.aclose()
        await client.aclose()
        _log.warning("agent download: upstream returned text/html, likely wrong URL")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Upstream returned HTML instead of a binary file",
        )

    # 透传关键响应头
    resp_headers: dict[str, str] = {}
    for hdr in ("content-length", "content-disposition", "etag", "last-modified"):
        val = upstream.headers.get(hdr)
        if val:
            resp_headers[hdr] = val

    # 上游声明的 Content-Length 超限时直接拒绝
    content_length = upstream.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_size:
                await upstream.aclose()
                await client.aclose()
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Upstream file exceeds maximum allowed size",
                )
        except ValueError:
            pass

    content_type = upstream.headers.get("content-type", "application/octet-stream")

    async def _stream_chunks():
        """逐 chunk 读取并转发上游数据，超限时中断."""
        transferred = 0
        try:
            async for chunk in upstream.aiter_bytes(_DOWNLOAD_CHUNK_SIZE):
                transferred += len(chunk)
                if transferred > max_size:
                    _log.warning(
                        "agent download: exceeded max size (%d), aborting", max_size,
                    )
                    break
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        _stream_chunks(),
        media_type=content_type,
        headers=resp_headers,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 安装脚本代理下载
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/install-script")
async def agent_install_script(
    token: str = Query(..., min_length=1, description="reg-token 或 server token"),
    db: AsyncSession = Depends(get_async_session),
):
    """代理下载安装脚本 — 流式转发，不在面板本地落盘.

    鉴权逻辑与 /agent/download 一致。
    上游 URL 从 agent_install_script_url 配置读取。
    """
    # ── 鉴权 ──
    valid = False
    global_token = config_cache.get("global_registration_token", "")
    if global_token and token == global_token:
        valid = True
    if not valid:
        if server_cache.get_uuid_by_token(token):
            valid = True
    if not valid:
        srv = await crud_clients.get_server_by_token(db, token)
        if srv:
            valid = True
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    # ── 读取并校验脚本 URL ──
    from app.core.config import settings as _settings
    script_url: str = config_cache.get("agent_install_script_url", "") or _settings.AGENT_INSTALL_SCRIPT_URL
    _validate_agent_url(script_url, "agent_install_script_url")

    # ── 流式代理 ──
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=_UPSTREAM_CONNECT_TIMEOUT,
            read=60,
            write=10,
            pool=10,
        ),
        follow_redirects=True,
        headers={"User-Agent": "Collei-Panel/1.0"},
    )

    try:
        req = client.build_request("GET", script_url)
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        _log.warning("install-script: upstream request failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to connect to agent_install_script_url upstream",
        )

    if upstream.status_code != 200:
        body_text = (await upstream.aread()).decode(errors="replace")[:200]
        await upstream.aclose()
        await client.aclose()
        _log.warning("install-script: upstream returned %s: %s", upstream.status_code, body_text)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Upstream returned HTTP {upstream.status_code}",
        )

    # 上游 Content-Length 超限时直接拒绝
    content_length = upstream.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _SCRIPT_MAX_SIZE:
                await upstream.aclose()
                await client.aclose()
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Upstream script exceeds maximum allowed size",
                )
        except ValueError:
            pass

    async def _stream_script():
        transferred = 0
        try:
            async for chunk in upstream.aiter_bytes(_DOWNLOAD_CHUNK_SIZE):
                transferred += len(chunk)
                if transferred > _SCRIPT_MAX_SIZE:
                    _log.warning(
                        "install-script: exceeded max size (%d), aborting",
                        _SCRIPT_MAX_SIZE,
                    )
                    break
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        _stream_script(),
        media_type="text/x-shellscript",
        headers={"Content-Disposition": "inline; filename=\"install.sh\""},
    )


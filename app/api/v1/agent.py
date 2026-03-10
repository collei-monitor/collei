"""Agent 端 API 路由（无需面板登录认证）.

端点:
  POST  /agent/register   Agent 自动注册（全局密钥）
  POST  /agent/verify     Agent 验证 token（被动注册）
  POST  /agent/report     Agent 混合上报（硬件 + 监控数据）
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.geoip import DEFAULT_DB, lookup_country
from app.core.server_cache import server_cache
from app.crud import clients as crud_clients
from app.crud import monitoring as crud_monitoring
from app.db.session import get_async_session
from app.schemas.agent import (
    AgentRegisterRequest,
    AgentRegisterResponse,
    AgentReportRequest,
    AgentReportResponse,
    AgentVerifyRequest,
    AgentVerifyResponse,
)

router = APIRouter(prefix="/agent", tags=["agent"])


# ─── 辅助函数 ─────────────────────────────────────────────────────────────────

async def _get_config_value(db: AsyncSession, key: str) -> str | None:
    """从 configs 表获取配置值."""
    from app.crud import config as crud_config
    return await crud_config.get_config_value(db, key)

async def _resolve_region(ipv4: str | None, ipv6: str | None, db: AsyncSession) -> str | None:
    """优先用 IPv4，否则用 IPv6 查询归属国家代码."""
    from app.crud import config as crud_config
    db_name = await crud_config.get_config_value(db, "ip_db") or DEFAULT_DB
    ip = ipv4 or ipv6
    return await lookup_country(ip, db_name)

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

    # 自动解析 IP 归属国家
    region = await _resolve_region(body.ipv4, body.ipv6, db)
    if region:
        hardware["region"] = region

    server = await crud_clients.create_server(
        db,
        name=body.name,
        is_approved=0,  # 自动注册需审核
        hardware_info=hardware,
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
    """Agent 被动注册验证 — 使用管理员下发的 token.

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

    # 自动解析 IP 归属国家
    region = await _resolve_region(body.ipv4, body.ipv6, db)
    if region:
        hardware["region"] = region

    await crud_clients.update_server_hardware(db, server.uuid, hardware)

    # 确保存在 server_status 记录
    await crud_clients.upsert_server_status(db, server.uuid)

    return AgentVerifyResponse(
        uuid=server.uuid,
        token=server.token,  # type: ignore[arg-type]
        is_approved=server.is_approved,
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
        region = await _resolve_region(body.ipv4, body.ipv6, db)
        if region:
            hardware_fields["region"] = region

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

    await crud_clients.upsert_server_status(
        db,
        server.uuid,
        **status_kwargs,
    )

    # ── 同步内存缓存 ──
    if hardware_fields:
        server_cache.update_server(server.uuid, hardware_fields)
    server_cache.update_status(
        server.uuid, status=1, last_online=now, boot_time=body.boot_time,
    )
    if load_dict:
        server_cache.update_load(server.uuid, load_dict)

    return AgentReportResponse(
        uuid=server.uuid,
        is_approved=server.is_approved,
    )

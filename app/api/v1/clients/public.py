"""公开接口 API 路由（无需认证 / 选择性登录）.

端点:
  GET  /clients/public/servers              获取公开服务器列表（游客过滤 hidden）
  GET  /clients/public/groups               获取分组列表与分组内服务器UUID列表
  GET  /clients/public/servers/{uuid}/load  获取指定服务器的监控数据（游客限制 hidden/is_approved）
  GET  /clients/public/servers/{uuid}/network 获取指定服务器的网络探测结果（游客限制 hidden/is_approved）
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_optional_user
from app.core.config_cache import config_cache
from app.crud import clients as crud
from app.crud import monitoring as crud_monitoring
from app.crud import network as crud_network
from app.db.session import get_async_session
from app.models.auth import User
from app.schemas.agent import LoadDataResponse, LoadNowRead
from app.schemas.clients import (
    GroupRead,
    GroupWithServersRead,
    ServerPublicBrief,
)
from app.schemas.network import NetworkStatusRead, NetworkTargetRead

from typing import Any

router = APIRouter()


@router.get("/public/servers", response_model=list[ServerPublicBrief])
async def list_servers_public(
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_session),
):
    """公开服务器列表。

    - 未登录：仅返回 hidden=0 且 is_approved=1 的服务器，不含敏感字段。
    - 已登录：返回全部服务器（含隐藏），不含敏感字段。
    """
    servers = await crud.get_all_servers(db)
    statuses = {s.uuid: s for s in await crud.get_all_server_statuses(db)}
    result = []
    for srv in servers:
        if current_user is None and (srv.hidden == 1 or srv.is_approved != 1):
            continue
        groups = await crud.get_server_groups(db, srv.uuid)
        st = statuses.get(srv.uuid)
        result.append(ServerPublicBrief(
            uuid=srv.uuid,
            name=srv.name,
            cpu_name=srv.cpu_name,
            arch=srv.arch,
            os=srv.os,
            region=srv.region,
            top=srv.top,
            status=st.status if st else 0,
            last_online=st.last_online if st else None,
            boot_time=st.boot_time if st else None,
            groups=[GroupRead.model_validate(g) for g in groups],
        ))
    return result


@router.get("/public/groups", response_model=list[GroupWithServersRead])
async def list_groups_public(
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_session),
):
    """公开分组列表及其服务器UUID列表。

    - 未登录：仅返回分组内 hidden=0 且 is_approved=1 的服务器UUID。
    - 已登录：返回分组内的全部服务器UUID。
    """
    groups = await crud.get_all_groups(db)
    result = []

    for group in groups:
        servers = await crud.get_group_servers(db, group.id)
        server_uuids = []

        for srv in servers:
            if current_user is None and (srv.hidden == 1 or srv.is_approved != 1):
                continue
            server_uuids.append(srv.uuid)

        result.append(GroupWithServersRead(
            id=group.id,
            name=group.name,
            top=group.top,
            created_at=group.created_at,
            server_uuids=server_uuids,
        ))

    return result


@router.get("/public/servers/{uuid}/load", response_model=LoadDataResponse)
async def get_server_load_public(
    uuid: str,
    range: int | None = Query(default=None, description="查询范围（小时）"),
    start_time: int | None = Query(default=None, description="查询起始时间戳"),
    end_time: int | None = Query(default=None, description="查询结束时间戳"),
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_session),
):
    """公开获取指定服务器的监控数据.

    - 未登录：仅允许查询 hidden=0 且 is_approved=1 的服务器。
    - 已登录：可查询任意服务器。

    查询方式（按优先级）：
      - start_time + end_time：指定时间段，根据跨度自动选择数据表。
      - range（小时）：查询过去 N 小时，根据范围自动选择数据表。
      - 均不传：返回 load_now 实时数据，同时返回 load_retain_seconds。

    数据表选择逻辑：
      - 跨度 ≤ load_minute_retain_hours → load_minute 表
      - 跨度 > load_minute_retain_hours → load_hour 表
    """
    server = await crud.get_server_by_uuid(db, uuid)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    if current_user is None and (server.hidden == 1 or server.is_approved != 1):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    minute_retain_hours = int(config_cache.get("load_minute_retain_hours") or 24)

    # 优先级 1: 时间段查询
    if start_time is not None and end_time is not None:
        now = int(time.time())
        span_hours = (end_time - start_time) / 3600
        earliest_offset_hours = (now - start_time) / 3600
        # 如果时间段跨度或最早时间点超出 minute 保留范围，使用 hour 表
        if span_hours > minute_retain_hours or earliest_offset_hours > minute_retain_hours:
            data = await crud_monitoring.get_load_hour_range(
                db, uuid, start_time=start_time, end_time=end_time,
            )
        else:
            data = await crud_monitoring.get_load_minute_range(
                db, uuid, start_time=start_time, end_time=end_time,
            )
        return LoadDataResponse(data=[LoadNowRead.model_validate(r) for r in data])

    # 优先级 2: 范围查询 (小时)
    if range is not None:
        now = int(time.time())
        query_start = now - range * 3600
        if range > minute_retain_hours:
            data = await crud_monitoring.get_load_hour_range(
                db, uuid, start_time=query_start, end_time=now,
            )
        else:
            data = await crud_monitoring.get_load_minute_range(
                db, uuid, start_time=query_start, end_time=now,
            )
        return LoadDataResponse(data=[LoadNowRead.model_validate(r) for r in data])

    # 优先级 3: 默认返回 load_now 实时数据，附带 load_retain_seconds
    retain = int(config_cache.get("load_retain_seconds") or 80)
    records = await crud_monitoring.get_load_now(db, uuid)
    return LoadDataResponse(
        load_retain_seconds=retain,
        data=[LoadNowRead.model_validate(r) for r in records],
    )


@router.get("/public/servers/{uuid}/network")
async def get_server_network_status_public(
    uuid: str,
    range: int | None = Query(default=None, alias="range", description="查询最近 N 小时"),
    start_time: int | None = Query(default=None, description="查询起始时间戳"),
    end_time: int | None = Query(default=None, description="查询结束时间戳"),
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_session),
):
    """公开获取指定服务器的所有网络探测结果，按目标分组。

    - 未登录：仅允许查询 hidden=0 且 is_approved=1 的服务器。
    - 已登录：可查询任意服务器。

        查询方式（按优先级）：
      - start_time + end_time：指定时间段内的所有记录。
      - range：查询最近 N 小时内的所有记录。
            - 均不传：每个监控目标返回最近 60 条记录（按时间倒序）。
    """
    server = await crud.get_server_by_uuid(db, uuid)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    if current_user is None and (server.hidden == 1 or server.is_approved != 1):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    query_start: int | None = start_time
    query_end: int | None = end_time
    query_limit: int | None = None
    if query_start is None and query_end is None and range is not None:
        now = int(time.time())
        query_start = now - range * 3600
        query_end = now
    elif query_start is None and query_end is None and range is None:
        query_limit = 60

    grouped = await crud_network.get_network_status_by_server_grouped(
        db, uuid,
        start_time=query_start,
        end_time=query_end,
        limit=query_limit,
    )

    # 加载目标信息
    target_ids = list(grouped.keys())
    targets_map: dict[int, Any] = {}
    for tid in target_ids:
        t = await crud_network.get_target(db, tid)
        if t:
            targets_map[tid] = t

    result = []
    for tid, records in grouped.items():
        target = targets_map.get(tid)
        result.append({
            "target": NetworkTargetRead.model_validate(target) if target else None,
            "records": [NetworkStatusRead.model_validate(r) for r in records],
        })

    return result

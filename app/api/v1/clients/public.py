"""公开接口 API 路由（无需认证 / 选择性登录）.

端点:
  GET  /clients/public/servers              获取公开服务器列表（游客过滤 hidden）
  GET  /clients/public/groups               获取分组列表与分组内服务器UUID列表
  GET  /clients/public/servers/{uuid}/load  获取指定服务器的监控数据（游客限制 hidden/is_approved）
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_optional_user
from app.crud import clients as crud
from app.crud import monitoring as crud_monitoring
from app.crud import network as crud_network
from app.db.session import get_async_session
from app.models.auth import User
from app.schemas.agent import LoadNowRead
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


@router.get("/public/servers/{uuid}/load", response_model=list[LoadNowRead])
async def get_server_load_public(
    uuid: str,
    range: int | None = Query(default=None, description="查询范围（小时），不传则返回 load_now 最新数据"),
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_session),
):
    """公开获取指定服务器的监控数据。

    - 未登录：仅允许查询 hidden=0 且 is_approved=1 的服务器。
    - 已登录：可查询任意服务器。

    查询方式：
      - 不传 range：返回 load_now 最新数据（约 1 分钟粒度）。
      - 传入 range（小时）：查询过去 N 小时的数据。
    """
    server = await crud.get_server_by_uuid(db, uuid)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    if current_user is None and (server.hidden == 1 or server.is_approved != 1):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    if range is None:
        records = await crud_monitoring.get_load_now(db, uuid)
    else:
        now = int(time.time())
        records = await crud_monitoring.get_load_range(
            db, uuid, start_time=now - range * 3600, end_time=now,
        )
    return records


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
      - 均不传：返回保留时间内的所有记录。
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
    if query_start is None and query_end is None and range is not None:
        now = int(time.time())
        query_start = now - range * 3600
        query_end = now

    grouped = await crud_network.get_network_status_by_server_grouped(
        db, uuid,
        start_time=query_start,
        end_time=query_end,
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

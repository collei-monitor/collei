"""监控数据查询 API 路由（需管理员登录）.

端点:
  GET  /clients/servers/{uuid}/status   获取服务器当前状态
  GET  /clients/servers/{uuid}/load     获取服务器监控数据
  GET  /clients/servers/{uuid}/traffic  获取小时级流量统计
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config_cache import config_cache
from app.crud import clients as crud
from app.crud import monitoring as crud_monitoring
from app.db.session import get_async_session
from app.models.auth import User
from app.schemas.agent import LoadNowRead
from app.schemas.clients import ServerStatusRead, TrafficHourlyStatRead

router = APIRouter()


# ── 服务器状态（需认证）────────────────────────────────────────────────────────

@router.get("/servers/{uuid}/status", response_model=ServerStatusRead)
async def get_server_status(
    uuid: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取服务器当前状态."""
    server = await crud.get_server_by_uuid(db, uuid)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    ss = await crud.get_server_status(db, uuid)
    if not ss:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server status not found",
        )
    return ss


# ── 服务器监控数据（需认证）──────────────────────────────────────────────────

@router.get("/servers/{uuid}/load", response_model=list[LoadNowRead])
async def get_server_load(
    uuid: str,
    limit: int = 60,
    range: int | None = Query(default=None, alias="range", description="查询范围（小时）"),
    start_time: int | None = None,
    end_time: int | None = None,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取服务器监控数据.

    查询方式（按优先级）:
      - start_time + end_time：指定时间段，根据跨度自动选择数据表。
      - range（小时）：查询过去 N 小时，根据范围自动选择数据表。
      - 默认: 返回 load_now 最近 N 条（limit 参数，默认 60）

    数据表选择逻辑：
      - 跨度 ≤ load_minute_retain_hours → load_minute 表
      - 跨度 > load_minute_retain_hours → load_hour 表
    """
    server = await crud.get_server_by_uuid(db, uuid)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    minute_retain_hours = int(config_cache.get("load_minute_retain_hours") or 24)

    # 优先级 1: 时间段查询
    if start_time is not None and end_time is not None:
        now = int(time.time())
        span_hours = (end_time - start_time) / 3600
        earliest_offset_hours = (now - start_time) / 3600
        if span_hours > minute_retain_hours or earliest_offset_hours > minute_retain_hours:
            return await crud_monitoring.get_load_hour_range(
                db, uuid, start_time=start_time, end_time=end_time,
            )
        return await crud_monitoring.get_load_minute_range(
            db, uuid, start_time=start_time, end_time=end_time,
        )

    # 优先级 2: 范围查询 (小时)
    if range is not None:
        now = int(time.time())
        query_start = now - range * 3600
        if range > minute_retain_hours:
            return await crud_monitoring.get_load_hour_range(
                db, uuid, start_time=query_start, end_time=now,
            )
        return await crud_monitoring.get_load_minute_range(
            db, uuid, start_time=query_start, end_time=now,
        )

    # 优先级 3: 默认返回 load_now 最新数据
    records = await crud_monitoring.get_load_now(db, uuid, limit=limit)
    return records


# ── 流量统计（需认证）──────────────────────────────────────────────────────────

@router.get(
    "/servers/{uuid}/traffic",
    response_model=list[TrafficHourlyStatRead],
)
async def get_traffic_stats(
    uuid: str,
    start_time: int = Query(..., description="查询起始时间戳"),
    end_time: int = Query(..., description="查询结束时间戳"),
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取服务器指定时间范围内的小时级流量统计."""
    server = await crud.get_server_by_uuid(db, uuid)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    return await crud_monitoring.get_traffic_hourly_range(
        db, uuid, start_time=start_time, end_time=end_time,
    )

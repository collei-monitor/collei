"""监控数据查询 API 路由（需管理员登录）.

端点:
  GET  /clients/servers/{uuid}/status   获取服务器当前状态
  GET  /clients/servers/{uuid}/load     获取服务器监控数据
  GET  /clients/servers/{uuid}/traffic  获取小时级流量统计
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
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
    start_time: int | None = None,
    end_time: int | None = None,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取服务器监控数据.

    支持两种查询方式:
      - 默认: 最近 N 条（limit 参数，默认 60）
      - 时间范围: 指定 start_time 和 end_time
    """
    server = await crud.get_server_by_uuid(db, uuid)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    if start_time is not None and end_time is not None:
        records = await crud_monitoring.get_load_range(
            db, uuid, start_time=start_time, end_time=end_time,
        )
    else:
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

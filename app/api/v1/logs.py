"""审计日志查询 API 路由（需管理员登录）.

端点:
  GET  /logs    查询审计日志（支持分页与多维过滤）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.crud import notification as crud_notification
from app.db.session import get_async_session
from app.models.auth import User
from app.schemas.notification import LogListResponse, LogRead

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("", response_model=LogListResponse)
async def list_logs(
    msg_type: str | None = Query(default=None, description="日志类型: auth, server, config, alert, task, error, billing, network, system"),
    level: str | None = Query(default=None, description="日志级别: info, warning, error"),
    server_uuid: str | None = Query(default=None, description="关联的服务器 UUID"),
    source: str | None = Query(default=None, description="来源模块"),
    start_time: int | None = Query(default=None, description="起始时间戳"),
    end_time: int | None = Query(default=None, description="结束时间戳"),
    limit: int = Query(default=100, ge=1, le=1000, description="每页条数"),
    offset: int = Query(default=0, ge=0, description="偏移量"),
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """查询审计日志，支持按类型、级别、服务器、来源、时间范围过滤."""
    items, total = await crud_notification.get_logs(
        db,
        msg_type=msg_type,
        level=level,
        server_uuid=server_uuid,
        source=source,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        offset=offset,
    )
    return LogListResponse(
        items=[LogRead.model_validate(item) for item in items],
        total=total,
    )

"""告警历史 API 路由（需管理员登录）.

端点:
  GET     /notifications/history                获取告警历史
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.crud import notification as crud
from app.db.session import get_async_session
from app.models.auth import User
from app.schemas.notification import AlertHistoryRead

router = APIRouter()


@router.get("/history", response_model=list[AlertHistoryRead])
async def list_alert_history(
    server_uuid: str | None = Query(None, description="按服务器 UUID 过滤"),
    rule_id: int | None = Query(None, description="按规则 ID 过滤"),
    limit: int = Query(50, ge=1, le=500, description="返回数量上限"),
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取告警历史记录."""
    return await crud.get_alert_history(
        db, server_uuid=server_uuid, rule_id=rule_id, limit=limit,
    )

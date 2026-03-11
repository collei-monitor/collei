"""通知渠道 API 路由（需管理员登录）.

端点:
  POST    /notifications/channels               创建通知渠道
  GET     /notifications/channels               获取所有渠道
  GET     /notifications/channels/{id}          获取单个渠道
  PUT     /notifications/channels/{id}          更新渠道
  DELETE  /notifications/channels/{id}          删除渠道
  POST    /notifications/channels/{id}/test     发送测试通知
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.notifier import send_notification
from app.crud import notification as crud
from app.db.session import get_async_session
from app.models.auth import User
from app.schemas.notification import (
    AlertChannelCreate,
    AlertChannelRead,
    AlertChannelUpdate,
    MessageResponse,
)

router = APIRouter()


async def _reload_engine() -> None:
    from app.core.alert_engine import alert_engine
    await alert_engine.reload()


@router.post(
    "/channels",
    response_model=AlertChannelRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_channel(
    body: AlertChannelCreate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """创建通知渠道."""
    provider = await crud.get_provider(db, body.provider_id)
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider id={body.provider_id} not found",
        )
    result = await crud.create_channel(
        db, name=body.name, provider_id=body.provider_id, target=body.target,
    )
    await _reload_engine()
    return result


@router.get("/channels", response_model=list[AlertChannelRead])
async def list_channels(
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取所有通知渠道."""
    return await crud.get_all_channels(db)


@router.get("/channels/{channel_id}", response_model=AlertChannelRead)
async def get_channel(
    channel_id: int,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取单个通知渠道."""
    channel = await crud.get_channel(db, channel_id)
    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    return channel


@router.put("/channels/{channel_id}", response_model=AlertChannelRead)
async def update_channel(
    channel_id: int,
    body: AlertChannelUpdate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """更新通知渠道."""
    channel = await crud.get_channel(db, channel_id)
    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )
    if "provider_id" in data:
        provider = await crud.get_provider(db, data["provider_id"])
        if not provider:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Provider id={data['provider_id']} not found",
            )
    result = await crud.update_channel(db, channel_id, **data)
    await _reload_engine()
    return result


@router.delete("/channels/{channel_id}", response_model=MessageResponse)
async def delete_channel(
    channel_id: int,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """删除通知渠道."""
    deleted = await crud.delete_channel(db, channel_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    await _reload_engine()
    return MessageResponse(message="Channel deleted")


@router.post("/channels/{channel_id}/test", response_model=MessageResponse)
async def test_channel(
    channel_id: int,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """向指定渠道发送一条测试通知."""
    channel = await crud.get_channel(db, channel_id)
    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    provider = await crud.get_provider(db, channel.provider_id)
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider id={channel.provider_id} not found",
        )
    channel_dict = {
        "name": channel.name,
        "target": channel.target,
        "provider_type": provider.type,
        "addition": provider.addition,
    }
    try:
        await send_notification(channel_dict, "这是一条来自 Collei 的测试通知 ✅")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"通知发送失败: {exc}",
        ) from exc
    return MessageResponse(message="测试通知已发送")

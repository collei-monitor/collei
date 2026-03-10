"""消息发送提供商 API 路由（需管理员登录）.

端点:
  POST    /notifications/providers              创建消息提供商
  GET     /notifications/providers              获取所有提供商
  GET     /notifications/providers/{name}       获取单个提供商
  PUT     /notifications/providers/{name}       更新提供商
  DELETE  /notifications/providers/{name}       删除提供商
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.crud import notification as crud
from app.db.session import get_async_session
from app.models.auth import User
from app.schemas.notification import (
    MessageResponse,
    ProviderCreate,
    ProviderRead,
    ProviderUpdate,
)

router = APIRouter()


async def _reload_engine() -> None:
    from app.core.alert_engine import alert_engine
    await alert_engine.reload()


@router.post(
    "/providers",
    response_model=ProviderRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_provider(
    body: ProviderCreate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """创建消息发送提供商."""
    existing = await crud.get_provider(db, body.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Provider already exists",
        )
    result = await crud.create_provider(db, name=body.name, addition=body.addition)
    await _reload_engine()
    return result


@router.get("/providers", response_model=list[ProviderRead])
async def list_providers(
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取所有消息发送提供商."""
    return await crud.get_all_providers(db)


@router.get("/providers/{name}", response_model=ProviderRead)
async def get_provider(
    name: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取单个消息发送提供商."""
    provider = await crud.get_provider(db, name)
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")
    return provider


@router.put("/providers/{name}", response_model=ProviderRead)
async def update_provider(
    name: str,
    body: ProviderUpdate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """更新消息发送提供商."""
    provider = await crud.get_provider(db, name)
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )
    result = await crud.update_provider(db, name, **data)
    await _reload_engine()
    return result


@router.delete("/providers/{name}", response_model=MessageResponse)
async def delete_provider(
    name: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """删除消息发送提供商（级联删除关联的渠道）."""
    deleted = await crud.delete_provider(db, name)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")
    await _reload_engine()
    return MessageResponse(message="Provider deleted")

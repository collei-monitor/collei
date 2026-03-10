"""服务器计费规则 API 路由（需管理员登录）.

端点:
  GET     /clients/servers/{uuid}/billing  获取服务器的计费规则
  PUT     /clients/servers/{uuid}/billing  创建或更新服务器的计费规则
  DELETE  /clients/servers/{uuid}/billing  删除服务器的计费规则
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.crud import clients as crud
from app.db.session import get_async_session
from app.models.auth import User
from app.schemas.clients import (
    BillingRuleCreate,
    BillingRuleRead,
    MessageResponse,
)

router = APIRouter()


@router.get("/servers/{uuid}/billing", response_model=BillingRuleRead | None)
async def get_billing_rule(
    uuid: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取服务器的计费规则."""
    server = await crud.get_server_by_uuid(db, uuid)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    return await crud.get_billing_rule(db, uuid)


@router.put("/servers/{uuid}/billing", response_model=BillingRuleRead)
async def upsert_billing_rule(
    uuid: str,
    body: BillingRuleCreate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """创建或更新服务器的计费规则."""
    server = await crud.get_server_by_uuid(db, uuid)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    data = body.model_dump(exclude_unset=True)
    return await crud.upsert_billing_rule(db, uuid, **data)


@router.delete("/servers/{uuid}/billing", response_model=MessageResponse)
async def delete_billing_rule(
    uuid: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """删除服务器的计费规则."""
    server = await crud.get_server_by_uuid(db, uuid)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    deleted = await crud.delete_billing_rule(db, uuid)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Billing rule not found")
    return MessageResponse(message="Billing rule deleted")

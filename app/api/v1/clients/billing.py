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
from app.core.server_cache import server_cache
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
    """创建或更新服务器的计费规则.

    新建计费规则时会自动启用服务器的 enable_statistics_mode。
    """
    server = await crud.get_server_by_uuid(db, uuid)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    is_new = await crud.get_billing_rule(db, uuid) is None
    data = body.model_dump(exclude_unset=True)
    rule = await crud.upsert_billing_rule(db, uuid, **data)

    # 同步缓存
    rule_dict = {f: getattr(rule, f, None) for f in (
        "uuid", "billing_cycle", "billing_cycle_data", "billing_cycle_cost",
        "traffic_reset_day", "traffic_threshold", "accounting_mode",
        "billing_cycle_cost_code", "expiry_date",
    )}
    server_cache.update_billing_rule(uuid, rule_dict)
    if is_new:
        server_cache.update_server(uuid, {"enable_statistics_mode": 1})
    return rule


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
    server_cache.remove_billing_rule(uuid)
    return MessageResponse(message="Billing rule deleted")

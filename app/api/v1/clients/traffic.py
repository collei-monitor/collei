"""服务器流量统计规则 API 路由（需管理员登录）.

端点:
  GET     /clients/servers/{uuid}/traffic-rule  获取服务器的流量统计规则
  PUT     /clients/servers/{uuid}/traffic-rule  创建或更新服务器的流量统计规则
  DELETE  /clients/servers/{uuid}/traffic-rule  删除服务器的流量统计规则
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
    MessageResponse,
    TrafficRuleCreate,
    TrafficRuleRead,
)

router = APIRouter()

_TRAFFIC_FIELDS = ("traffic_reset_day", "traffic_threshold", "accounting_mode")


@router.get("/servers/{uuid}/traffic-rule", response_model=TrafficRuleRead | None)
async def get_traffic_rule(
    uuid: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取服务器的流量统计规则."""
    server = await crud.get_server_by_uuid(db, uuid)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    rule = await crud.get_billing_rule(db, uuid)
    if not rule:
        return None
    return TrafficRuleRead(
        uuid=rule.uuid,
        traffic_reset_day=rule.traffic_reset_day,
        traffic_threshold=rule.traffic_threshold,
        accounting_mode=rule.accounting_mode,
    )


@router.put("/servers/{uuid}/traffic-rule", response_model=TrafficRuleRead)
async def upsert_traffic_rule(
    uuid: str,
    body: TrafficRuleCreate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """创建或更新服务器的流量统计规则.

    仅当 accounting_mode 或 traffic_reset_day 发生变化时才重新计算周期流量；
    单独修改 traffic_threshold 不会触发重算。
    """
    server = await crud.get_server_by_uuid(db, uuid)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    old_rule = await crud.get_billing_rule(db, uuid)
    data = body.model_dump(exclude_unset=True)

    # 检测是否需要重新计算流量（accounting_mode 或 traffic_reset_day 变化）
    need_recalc = False
    if old_rule:
        if ("accounting_mode" in data
                and data["accounting_mode"] != old_rule.accounting_mode):
            need_recalc = True
        if ("traffic_reset_day" in data
                and data["traffic_reset_day"] != old_rule.traffic_reset_day):
            need_recalc = True
    else:
        # 新建规则 → 需要计算
        need_recalc = True

    rule = await crud.upsert_billing_rule(db, uuid, **data)

    # 同步缓存（仅流量相关字段）
    cache_dict = {f: getattr(rule, f, None) for f in _TRAFFIC_FIELDS}
    cache_dict["uuid"] = uuid
    server_cache.update_billing_rule(uuid, cache_dict)

    # 仅在计算参数变化时重新计算周期流量
    if need_recalc:
        from app.crud.monitoring import get_cycle_traffic
        traffic_used = await get_cycle_traffic(
            db, uuid,
            traffic_reset_day=rule.traffic_reset_day or 0,
            billing_cycle_data=rule.billing_cycle_data,
            accounting_mode=rule.accounting_mode,
        )
        server_cache.update_cycle_traffic(uuid, traffic_used)

    return TrafficRuleRead(
        uuid=rule.uuid,
        traffic_reset_day=rule.traffic_reset_day,
        traffic_threshold=rule.traffic_threshold,
        accounting_mode=rule.accounting_mode,
    )


@router.delete("/servers/{uuid}/traffic-rule", response_model=MessageResponse)
async def delete_traffic_rule(
    uuid: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """清除服务器的流量统计规则（将流量相关字段置空）."""
    server = await crud.get_server_by_uuid(db, uuid)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    rule = await crud.get_billing_rule(db, uuid)
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Traffic rule not found")
    # 将流量相关字段置 None
    await crud.upsert_billing_rule(
        db, uuid,
        traffic_reset_day=None,
        traffic_threshold=None,
        accounting_mode=None,
    )
    # 清除缓存中的流量数据
    server_cache.update_billing_rule(uuid, {
        "traffic_reset_day": None,
        "traffic_threshold": None,
        "accounting_mode": None,
    })
    server_cache.update_cycle_traffic(uuid, 0)
    return MessageResponse(message="Traffic rule cleared")

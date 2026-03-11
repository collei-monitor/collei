"""告警规则及映射 API 路由（需管理员登录）.

端点:
  POST    /notifications/rules                  创建告警规则
  GET     /notifications/rules                  获取所有规则
  GET     /notifications/rules/{id}             获取单个规则
  PUT     /notifications/rules/{id}             更新规则
  DELETE  /notifications/rules/{id}             删除规则

  GET     /notifications/rules/{id}/mappings           获取规则映射
  POST    /notifications/rules/{id}/mappings           添加规则映射
  DELETE  /notifications/rules/{id}/mappings           删除单条映射
  DELETE  /notifications/rules/{id}/mappings/all       删除规则所有映射
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.crud import notification as crud
from app.db.session import get_async_session
from app.models.auth import User
from app.schemas.notification import (
    AlertRuleCreate,
    AlertRuleMappingBatchCreate,
    AlertRuleMappingBatchRead,
    AlertRuleMappingCreate,
    AlertRuleMappingRead,
    AlertRuleRead,
    AlertRuleUpdate,
    MessageResponse,
)

router = APIRouter()


async def _reload_engine() -> None:
    from app.core.alert_engine import alert_engine
    await alert_engine.reload()


# ── 告警规则 CRUD ─────────────────────────────────────────────────────────────

@router.post(
    "/rules",
    response_model=AlertRuleRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_rule(
    body: AlertRuleCreate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """创建告警规则."""
    result = await crud.create_rule(db, **body.model_dump())
    await _reload_engine()
    return result


@router.get("/rules", response_model=list[AlertRuleRead])
async def list_rules(
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取所有告警规则."""
    return await crud.get_all_rules(db)


@router.get("/rules/{rule_id}", response_model=AlertRuleRead)
async def get_rule(
    rule_id: int,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取单个告警规则."""
    rule = await crud.get_rule(db, rule_id)
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    return rule


@router.put("/rules/{rule_id}", response_model=AlertRuleRead)
async def update_rule(
    rule_id: int,
    body: AlertRuleUpdate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """更新告警规则."""
    rule = await crud.get_rule(db, rule_id)
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )
    result = await crud.update_rule(db, rule_id, **data)
    await _reload_engine()
    return result


@router.delete("/rules/{rule_id}", response_model=MessageResponse)
async def delete_rule(
    rule_id: int,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """删除告警规则（级联删除映射和历史）."""
    deleted = await crud.delete_rule(db, rule_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    await _reload_engine()
    return MessageResponse(message="Rule deleted")


# ── 告警规则映射 ──────────────────────────────────────────────────────────────

@router.get("/rules/{rule_id}/mappings", response_model=list[AlertRuleMappingRead])
async def list_rule_mappings(
    rule_id: int,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取告警规则的所有映射."""
    rule = await crud.get_rule(db, rule_id)
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    return await crud.get_rule_mappings(db, rule_id)


@router.post(
    "/rules/{rule_id}/mappings",
    response_model=AlertRuleMappingBatchRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_rule_mappings(
    rule_id: int,
    body: AlertRuleMappingBatchCreate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """为告警规则批量添加映射（支持多个服务器或组）."""
    rule = await crud.get_rule(db, rule_id)
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")

    if body.target_type not in ("server", "group", "global"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="target_type must be 'server', 'group', or 'global'",
        )

    channel = await crud.get_channel(db, body.channel_id)
    if not channel:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Channel '{body.channel_id}' not found",
        )

    created, skipped = await crud.create_rule_mappings_batch(
        db,
        rule_id=rule_id,
        target_type=body.target_type,
        target_ids=body.target_ids,
        channel_id=body.channel_id,
    )
    if created:
        await _reload_engine()
    return AlertRuleMappingBatchRead(
        created=[AlertRuleMappingRead.model_validate(m) for m in created],
        skipped=skipped,
    )


@router.delete("/rules/{rule_id}/mappings", response_model=MessageResponse)
async def delete_rule_mapping(
    rule_id: int,
    target_type: str = Query(..., description="server / group / global"),
    target_id: str = Query(..., description="server_uuid / group_id"),
    channel_id: int = Query(..., description="channel id"),
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """删除告警规则的单条映射."""
    deleted = await crud.delete_rule_mapping(
        db, rule_id=rule_id, target_type=target_type, target_id=target_id,
        channel_id=channel_id,
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Mapping not found")
    await _reload_engine()
    return MessageResponse(message="Mapping deleted")


@router.delete("/rules/{rule_id}/mappings/all", response_model=MessageResponse)
async def delete_all_rule_mappings(
    rule_id: int,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """删除告警规则的所有映射."""
    rule = await crud.get_rule(db, rule_id)
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    count = await crud.delete_all_rule_mappings(db, rule_id)
    await _reload_engine()
    return MessageResponse(message=f"{count} mapping(s) deleted")

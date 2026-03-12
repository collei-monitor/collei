"""告警规则及目标/渠道绑定 API 路由（需管理员登录）.

端点:
  POST    /notifications/rules                  创建告警规则
  GET     /notifications/rules                  获取所有规则
  GET     /notifications/rules/{id}             获取单个规则
  PUT     /notifications/rules/{id}             更新规则
  DELETE  /notifications/rules/{id}             删除规则

  GET     /notifications/rules/{id}/targets            获取规则目标绑定
  POST    /notifications/rules/{id}/targets            批量添加规则目标绑定
  DELETE  /notifications/rules/{id}/targets            批量删除规则目标绑定

  GET     /notifications/rules/{id}/channels           获取规则渠道绑定
  PUT     /notifications/rules/{id}/channels           完全替换规则渠道绑定
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.crud import notification as crud
from app.db.session import get_async_session
from app.models.auth import User
from app.schemas.notification import (
    AlertRuleCreate,
    AlertRuleRead,
    AlertRuleUpdate,
    AlertRuleTargetBatchRequest,
    AlertRuleTargetRead,
    AlertRuleChannelSetRequest,
    AlertRuleChannelRead,
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


# ── 告警规则目标绑定 ──────────────────────────────────────────────────────────

@router.get("/rules/{rule_id}/targets", response_model=list[AlertRuleTargetRead])
async def list_rule_targets(
    rule_id: int,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取告警规则的所有目标绑定."""
    rule = await crud.get_rule(db, rule_id)
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    return await crud.get_rule_targets(db, rule_id)


@router.post(
    "/rules/{rule_id}/targets",
    response_model=list[AlertRuleTargetRead],
    status_code=status.HTTP_201_CREATED,
)
async def add_rule_targets(
    rule_id: int,
    body: AlertRuleTargetBatchRequest,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """为告警规则批量添加目标绑定（已存在的自动跳过）."""
    rule = await crud.get_rule(db, rule_id)
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")

    for t in body.targets:
        if t.target_type not in ("server", "group", "global"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="target_type must be 'server', 'group', or 'global'",
            )

    created, _skipped = await crud.add_rule_targets(
        db,
        rule_id=rule_id,
        targets=[t.model_dump() for t in body.targets],
    )
    if created:
        await _reload_engine()
    return created


@router.delete("/rules/{rule_id}/targets", response_model=MessageResponse)
async def delete_rule_targets(
    rule_id: int,
    body: AlertRuleTargetBatchRequest,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """批量删除告警规则的指定目标绑定."""
    rule = await crud.get_rule(db, rule_id)
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")

    count = await crud.delete_rule_targets_batch(
        db,
        rule_id=rule_id,
        items=[t.model_dump() for t in body.targets],
    )
    if count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No matching targets found",
        )
    await _reload_engine()
    return MessageResponse(message=f"{count} target(s) deleted")


# ── 告警规则渠道绑定 ──────────────────────────────────────────────────────────

@router.get("/rules/{rule_id}/channels", response_model=list[AlertRuleChannelRead])
async def list_rule_channels(
    rule_id: int,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取告警规则绑定的所有通知渠道."""
    rule = await crud.get_rule(db, rule_id)
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    return await crud.get_rule_channels(db, rule_id)


@router.put("/rules/{rule_id}/channels", response_model=list[AlertRuleChannelRead])
async def set_rule_channels(
    rule_id: int,
    body: AlertRuleChannelSetRequest,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """完全替换告警规则绑定的通知渠道（覆盖写入）."""
    rule = await crud.get_rule(db, rule_id)
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")

    # 验证渠道存在
    for cid in body.channel_ids:
        ch = await crud.get_channel(db, cid)
        if not ch:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Channel {cid} not found",
            )

    result = await crud.set_rule_channels(
        db,
        rule_id=rule_id,
        channel_ids=body.channel_ids,
    )
    await _reload_engine()
    return result

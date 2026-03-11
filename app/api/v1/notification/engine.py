"""告警引擎状态机 API 路由（需管理员登录）.

端点:
  GET     /notifications/engine/status          获取引擎运行概览
  GET     /notifications/engine/states          获取所有状态机条目
  GET     /notifications/engine/states/firing   仅获取 FIRING 状态
  GET     /notifications/engine/states/pending  仅获取 PENDING 状态
  POST    /notifications/engine/reload          手动热重载引擎配置
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.core.alert_engine import alert_engine
from app.models.auth import User
from app.schemas.notification import (
    AlertEngineStatus,
    AlertStateItem,
    MessageResponse,
)

router = APIRouter()


@router.get("/engine/status", response_model=AlertEngineStatus)
async def get_engine_status(
    _current_user: User = Depends(get_current_user),
):
    """获取告警引擎运行状态概览."""
    return alert_engine.get_status()


@router.get("/engine/states", response_model=list[AlertStateItem])
async def list_engine_states(
    _current_user: User = Depends(get_current_user),
):
    """获取所有状态机条目快照."""
    return alert_engine.get_all_states()


@router.get("/engine/states/firing", response_model=list[AlertStateItem])
async def list_firing_states(
    _current_user: User = Depends(get_current_user),
):
    """仅获取当前 FIRING（告警中）的状态机条目."""
    return [
        s for s in alert_engine.get_all_states()
        if s["status"] == "firing"
    ]


@router.get("/engine/states/pending", response_model=list[AlertStateItem])
async def list_pending_states(
    _current_user: User = Depends(get_current_user),
):
    """仅获取当前 PENDING（待触发）的状态机条目."""
    return [
        s for s in alert_engine.get_all_states()
        if s["status"] == "pending"
    ]


@router.post("/engine/reload", response_model=MessageResponse)
async def reload_engine(
    _current_user: User = Depends(get_current_user),
):
    """手动热重载告警引擎配置（从数据库重新加载规则/映射/渠道）."""
    await alert_engine.reload()
    status = alert_engine.get_status()
    return MessageResponse(
        message=f"Reloaded: {status['rules_count']} rules, "
                f"{status['mappings_count']} mappings",
    )

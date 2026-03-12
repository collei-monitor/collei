"""告警与通知的 Pydantic 请求/响应模型."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# 通用响应
# ═══════════════════════════════════════════════════════════════════════════════

class MessageResponse(BaseModel):
    message: str


# ═══════════════════════════════════════════════════════════════════════════════
# Message Sender Provider
# ═══════════════════════════════════════════════════════════════════════════════

class ProviderCreate(BaseModel):
    """创建消息发送提供商."""
    name: str | None = Field(None, max_length=64)
    type: str | None = Field(None, max_length=64)
    addition: str | None = None


class ProviderUpdate(BaseModel):
    """更新消息发送提供商."""
    name: str | None = Field(None, max_length=64)
    type: str | None = Field(None, max_length=64)
    addition: str | None = None


class ProviderRead(BaseModel):
    id: int
    name: str | None = None
    type: str | None = None
    addition: str | None = None

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════════════════════
# Alert Channel
# ═══════════════════════════════════════════════════════════════════════════════

class AlertChannelCreate(BaseModel):
    """创建通知渠道."""
    name: str = Field(..., min_length=1, max_length=128)
    provider_id: int
    target: str | None = None


class AlertChannelUpdate(BaseModel):
    """更新通知渠道."""
    name: str | None = Field(None, min_length=1, max_length=128)
    provider_id: int | None = None
    target: str | None = None


class AlertChannelRead(BaseModel):
    id: int
    name: str
    provider_id: int
    target: str | None = None

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════════════════════
# Alert Rule
# ═══════════════════════════════════════════════════════════════════════════════

class AlertRuleCreate(BaseModel):
    """创建告警规则."""
    name: str = Field(..., min_length=1, max_length=128)
    metric: str = Field(..., min_length=1,
                        description="监控指标: cpu, ram, offline, traffic_out 等")
    condition: str = Field(..., description="触发条件: >, <, ==")
    threshold: float = Field(..., description="阈值")
    duration: int = Field(60, ge=0, description="持续时间阈值(秒)")
    enabled: int = Field(0, ge=0, le=1)
    notify_recovery: int = Field(0, ge=0, le=1, description="是否启用恢复通知")


class AlertRuleUpdate(BaseModel):
    """更新告警规则."""
    name: str | None = Field(None, min_length=1, max_length=128)
    metric: str | None = Field(None, min_length=1)
    condition: str | None = None
    threshold: float | None = None
    duration: int | None = Field(None, ge=0)
    enabled: int | None = Field(None, ge=0, le=1)
    notify_recovery: int | None = Field(None, ge=0, le=1)


class AlertRuleRead(BaseModel):
    id: int
    name: str
    metric: str
    condition: str
    threshold: float
    duration: int = 60
    enabled: int = 0
    notify_recovery: int = 0
    created_at: int | None = None

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════════════════════
# Alert Rule Targets (规则目标绑定)
# ═══════════════════════════════════════════════════════════════════════════════

class AlertRuleTargetItem(BaseModel):
    """单条目标绑定."""
    target_type: str = Field(..., description="server / group / global")
    target_id: str = Field(..., min_length=1, description="UUID / group_id / 'all'")
    is_exclude: int = Field(0, ge=0, le=1, description="0=生效, 1=排除")


class AlertRuleTargetBatchRequest(BaseModel):
    """批量添加或删除规则的目标绑定."""
    targets: list[AlertRuleTargetItem] = Field(
        ..., min_length=1, description="目标绑定列表")


class AlertRuleTargetRead(BaseModel):
    rule_id: int
    target_type: str
    target_id: str
    is_exclude: int = 0

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════════════════════
# Alert Rule Channels (规则渠道绑定)
# ═══════════════════════════════════════════════════════════════════════════════

class AlertRuleChannelSetRequest(BaseModel):
    """完全替换规则绑定的通知渠道列表."""
    channel_ids: list[int] = Field(..., description="通知渠道 ID 列表")


class AlertRuleChannelRead(BaseModel):
    rule_id: int
    channel_id: int

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════════════════════
# Alert History
# ═══════════════════════════════════════════════════════════════════════════════

class AlertHistoryRead(BaseModel):
    id: int
    server_uuid: str
    rule_id: int
    status: str
    value: float | None = None
    created_at: int | None = None
    updated_at: int | None = None

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════════════════════
# Alert Engine (状态机实时状态)
# ═══════════════════════════════════════════════════════════════════════════════

class AlertStateItem(BaseModel):
    """单条告警状态机条目."""
    server_uuid: str
    server_name: str | None = None
    rule_id: int
    rule_name: str | None = None
    metric: str | None = None
    status: str
    value: float
    pending_since: float = 0.0
    last_notified_at: float = 0.0


class AlertEngineStatus(BaseModel):
    """告警引擎整体状态概览."""
    running: bool
    rules_count: int
    mappings_count: int
    states_count: int
    firing_count: int
    pending_count: int

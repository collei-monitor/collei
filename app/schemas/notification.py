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


class AlertRuleUpdate(BaseModel):
    """更新告警规则."""
    name: str | None = Field(None, min_length=1, max_length=128)
    metric: str | None = Field(None, min_length=1)
    condition: str | None = None
    threshold: float | None = None
    duration: int | None = Field(None, ge=0)
    enabled: int | None = Field(None, ge=0, le=1)


class AlertRuleRead(BaseModel):
    id: int
    name: str
    metric: str
    condition: str
    threshold: float
    duration: int = 60
    enabled: int = 0
    created_at: int | None = None

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════════════════════
# Alert Rule Mapping
# ═══════════════════════════════════════════════════════════════════════════════

class AlertRuleMappingCreate(BaseModel):
    """创建告警规则映射."""
    target_type: str = Field(..., description="server / group / global")
    target_id: str = Field(..., min_length=1)
    channel_id: int


class AlertRuleMappingRead(BaseModel):
    rule_id: int
    target_type: str
    target_id: str
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

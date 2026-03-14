"""网络监控相关的 Pydantic 请求/响应模型."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# NetworkTarget — 监控目标
# ═══════════════════════════════════════════════════════════════════════════════

class NetworkTargetCreate(BaseModel):
    """创建网络监控目标."""
    name: str = Field(..., min_length=1, max_length=256, description="目标别名")
    host: str = Field(..., min_length=1, max_length=512, description="目标 IP 或域名")
    protocol: str = Field("icmp", description="检测协议: icmp / tcp / http")
    port: int | None = Field(None, ge=1, le=65535, description="目标端口")
    interval: int = Field(60, ge=5, le=3600, description="探测间隔时间 (秒)")
    enabled: int = Field(1, ge=0, le=1, description="启停状态: 0=停用, 1=启用")


class NetworkTargetUpdate(BaseModel):
    """更新网络监控目标（所有字段可选）."""
    name: str | None = Field(None, min_length=1, max_length=256)
    host: str | None = Field(None, min_length=1, max_length=512)
    protocol: str | None = Field(None, description="icmp / tcp / http")
    port: int | None = Field(None, ge=1, le=65535)
    interval: int | None = Field(None, ge=5, le=3600)
    enabled: int | None = Field(None, ge=0, le=1)


class NetworkTargetRead(BaseModel):
    """返回给前端的监控目标数据."""
    id: int
    name: str
    host: str
    protocol: str
    port: int | None = None
    interval: int
    enabled: int

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════════════════════
# NetworkTargetDispatch — 下发节点
# ═══════════════════════════════════════════════════════════════════════════════

class DispatchEntry(BaseModel):
    """单条下发节点配置."""
    node_type: str = Field(..., description="节点类型: server / global")
    node_id: str = Field(..., description="server_uuid 或 'all'")
    is_exclude: int = Field(0, ge=0, le=1, description="0=生效, 1=排除")


class DispatchRead(BaseModel):
    """返回给前端的下发节点数据."""
    target_id: int
    node_type: str
    node_id: str
    is_exclude: int

    model_config = {"from_attributes": True}


class DispatchSetRequest(BaseModel):
    """全量设置目标的下发节点列表."""
    dispatches: list[DispatchEntry]


class NetworkTargetDetail(BaseModel):
    """目标详情（含下发节点）."""
    target: NetworkTargetRead
    dispatches: list[DispatchRead] = []


# ═══════════════════════════════════════════════════════════════════════════════
# NetworkStatus — 探测结果
# ═══════════════════════════════════════════════════════════════════════════════

class NetworkStatusRead(BaseModel):
    """返回给前端的探测结果."""
    target_id: int
    server_uuid: str
    time: int
    median_latency: float | None = None
    max_latency: float | None = None
    min_latency: float | None = None
    packet_loss: float = 0

    model_config = {"from_attributes": True}


class NetworkStatusLatest(BaseModel):
    """某个目标下每个节点的最新探测结果."""
    target_id: int
    server_uuid: str
    server_name: str | None = None
    time: int
    median_latency: float | None = None
    max_latency: float | None = None
    min_latency: float | None = None
    packet_loss: float = 0


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 上报用
# ═══════════════════════════════════════════════════════════════════════════════

class NetworkProbeResult(BaseModel):
    """Agent 上报的单条探测结果."""
    target_id: int = Field(..., description="关联的 network_targets.id")
    time: int | None = Field(None, description="探测完成时间戳，为空时后端自动填充")
    median_latency: float | None = Field(None, ge=0, description="中位数延迟 (ms)")
    max_latency: float | None = Field(None, ge=0, description="最大延迟 (ms)")
    min_latency: float | None = Field(None, ge=0, description="最小延迟 (ms)")
    packet_loss: float = Field(0, ge=0, le=100, description="丢包率 (%)")


class AgentDispatchTarget(BaseModel):
    """下发给 Agent 的探测目标."""
    id: int
    name: str
    host: str
    protocol: str
    port: int | None = None
    interval: int


class AgentDispatchResponse(BaseModel):
    """Agent 上报时返回的探测任务信息."""
    version: str = Field(..., description="目标列表版本哈希")
    targets: list[AgentDispatchTarget] | None = Field(
        None, description="更新的目标列表，为 null 表示无变更",
    )

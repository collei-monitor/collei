"""客户端与节点管理的 Pydantic 请求/响应模型."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# 通用响应
# ═══════════════════════════════════════════════════════════════════════════════

class MessageResponse(BaseModel):
    message: str


# ═══════════════════════════════════════════════════════════════════════════════
# Server
# ═══════════════════════════════════════════════════════════════════════════════

class ServerCreate(BaseModel):
    """管理员被动注册：手动创建服务器记录（仅需名称）."""
    name: str = Field(..., min_length=1, max_length=128)
    remark: str | None = None


class ServerUpdate(BaseModel):
    """管理员更新服务器信息."""
    name: str | None = Field(None, min_length=1, max_length=128)
    remark: str | None = None
    top: int | None = None
    hidden: int | None = Field(None, ge=0, le=1)
    region: str | None = None
    enable_statistics_mode: int | None = Field(None, ge=0, le=1)


class ServerRead(BaseModel):
    """返回给前端的服务器信息."""
    uuid: str
    name: str
    token: str | None = None
    cpu_name: str | None = None
    virtualization: str | None = None
    arch: str | None = None
    cpu_cores: int | None = None
    os: str | None = None
    kernel_version: str | None = None
    ipv4: str | None = None
    ipv6: str | None = None
    region: str | None = None
    mem_total: int | None = None
    swap_total: int | None = None
    disk_total: int | None = None
    version: str | None = None
    remark: str | None = None
    top: int = 0
    hidden: int = 0
    is_approved: int = 0
    enable_statistics_mode: int = 0
    created_at: int | None = None

    model_config = {"from_attributes": True}


class ServerBrief(BaseModel):
    """服务器简要信息 — 列表展示用，不含 token."""
    uuid: str
    name: str
    cpu_name: str | None = None
    arch: str | None = None
    os: str | None = None
    region: str | None = None
    ipv4: str | None = None
    ipv6: str | None = None
    version: str | None = None
    top: int = 0
    hidden: int = 0
    is_approved: int = 0
    enable_statistics_mode: int = 0
    created_at: int | None = None

    # 内联状态
    status: int = 0
    last_online: int | None = None
    boot_time: int | None = None

    # 所属分组
    groups: list[GroupRead] = []

    model_config = {"from_attributes": True}


class ServerCreateResponse(BaseModel):
    """被动注册成功后的响应（含 token & install 命令）."""
    uuid: str
    name: str
    token: str
    install_hint: str


class ServerPublicBrief(BaseModel):
    """公开接口返回的服务器信息 — 不含敏感字段（IP、版本、备注等）."""
    uuid: str
    name: str
    cpu_name: str | None = None
    arch: str | None = None
    os: str | None = None
    region: str | None = None
    top: int = 0

    # 内联状态
    status: int = 0
    last_online: int | None = None
    boot_time: int | None = None

    # 所属分组
    groups: list[GroupRead] = []

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════════════════════
# Server Status
# ═══════════════════════════════════════════════════════════════════════════════

class ServerStatusRead(BaseModel):
    uuid: str
    status: int = 0
    last_online: int | None = None
    current_run_id: str | None = None
    boot_time: int | None = None
    total_flow_out: int | None = None
    total_flow_in: int | None = None

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════════════════════
# Group
# ═══════════════════════════════════════════════════════════════════════════════

class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    top: int | None = None
    server_uuids: list[str] = Field(default_factory=list, description="创建时关联的服务器 UUID 列表")


class GroupUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    top: int | None = None
    server_uuids: list[str] | None = Field(None, description="要关联的服务器 UUID 列表（覆盖原有关联）")


class GroupRead(BaseModel):
    id: str
    name: str
    top: int | None = None
    created_at: int | None = None

    model_config = {"from_attributes": True}

class GroupWithServersRead(BaseModel):
    """分组及其内的服务器UUID列表 — 公开接口返回."""
    id: str
    name: str
    top: int | None = None
    created_at: int | None = None
    server_uuids: list[str] = []

    model_config = {"from_attributes": True}

# ═══════════════════════════════════════════════════════════════════════════════
# Server ↔ Group
# ═══════════════════════════════════════════════════════════════════════════════

class ServerGroupSet(BaseModel):
    """设置服务器所属分组（全量替换）."""
    group_ids: list[str] = Field(default_factory=list)


class ServerTopUpdate(BaseModel):
    """批量更新服务器 top 值的请求模型."""
    updates: dict[str, int] = Field(
        ..., description="格式为 {uuid: top_value} 的字典，例如 {'uuid-1': 100, 'uuid-2': 200}"
    )


class ServerTopUpdateResponse(BaseModel):
    """批量更新 top 值的响应."""
    total: int = Field(..., description="请求更新的服务器总数")
    updated: int = Field(..., description="成功更新的服务器数量")
    failed: int = Field(..., description="更新失败的服务器数量")
    failed_uuids: list[str] = Field(default_factory=list, description="更新失败的 UUID 列表")


class GroupTopUpdate(BaseModel):
    """批量更新分组 top 值的请求模型."""
    updates: dict[str, int] = Field(
        ..., description="格式为 {group_id: top_value} 的字典，例如 {'id-1': 100, 'id-2': 200}"
    )


class GroupTopUpdateResponse(BaseModel):
    """批量更新分组 top 值的响应."""
    total: int = Field(..., description="请求更新的分组总数")
    updated: int = Field(..., description="成功更新的分组数量")
    failed: int = Field(..., description="更新失败的分组数量")
    failed_ids: list[str] = Field(default_factory=list, description="更新失败的分组 ID 列表")


# ═══════════════════════════════════════════════════════════════════════════════
# Server Billing Rules
# ═══════════════════════════════════════════════════════════════════════════════

class BillingRuleCreate(BaseModel):
    """创建/更新服务器计费规则."""
    billing_cycle: int | None = Field(None, description="计费周期数值")
    billing_cycle_data: int | None = Field(None, description="计费周期数据")
    billing_cycle_cost: float | None = Field(None, description="周期费用")
    traffic_reset_day: int | None = Field(None, description="流量重置日")
    traffic_threshold: int | None = Field(None, ge=0, description="周期流量阈值 (Bytes)")
    accounting_mode: int | None = Field(None, ge=1, le=5,
        description="流量计算模式: 1-仅出站 2-仅入站 3-进出总和 4-取最大 5-取最小")


class BillingRuleRead(BaseModel):
    """服务器计费规则读取模型."""
    uuid: str
    billing_cycle: int | None = None
    billing_cycle_data: int | None = None
    billing_cycle_cost: float | None = None
    traffic_reset_day: int | None = None
    traffic_threshold: int | None = None
    accounting_mode: int | None = None

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════════════════════
# Traffic Hourly Stats
# ═══════════════════════════════════════════════════════════════════════════════

class TrafficHourlyStatRead(BaseModel):
    """流量统计读取模型."""
    server_uuid: str
    time: int
    net_in: int = 0
    net_out: int = 0

    model_config = {"from_attributes": True}

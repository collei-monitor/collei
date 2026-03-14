"""Agent 端与监控数据的 Pydantic 请求/响应模型."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 注册 / 验证
# ═══════════════════════════════════════════════════════════════════════════════

class AgentRegisterRequest(BaseModel):
    """Agent 自动注册请求 — 携带全局密钥 + 硬件信息."""
    reg_token: str = Field(..., description="全局安装密钥")
    name: str = Field(..., min_length=1, max_length=128, description="服务器名称")
    cpu_name: str | None = None
    virtualization: str | None = None
    arch: str | None = None
    cpu_cores: int | None = None
    os: str | None = None
    kernel_version: str | None = None
    ipv4: str | None = None
    ipv6: str | None = None
    mem_total: int | None = None
    swap_total: int | None = None
    disk_total: int | None = None
    version: str | None = None


class AgentRegisterResponse(BaseModel):
    """Agent 注册成功后返回专属凭证."""
    uuid: str
    token: str


class AgentVerifyRequest(BaseModel):
    """Agent 被动注册验证 — 使用管理员下发的 token."""
    token: str = Field(..., description="管理员下发的专属 token")
    name: str | None = Field(None, max_length=128, description="服务器名称（可选更新）")
    cpu_name: str | None = None
    virtualization: str | None = None
    arch: str | None = None
    cpu_cores: int | None = None
    os: str | None = None
    kernel_version: str | None = None
    ipv4: str | None = None
    ipv6: str | None = None
    mem_total: int | None = None
    swap_total: int | None = None
    disk_total: int | None = None
    version: str | None = None


class AgentVerifyResponse(BaseModel):
    """Agent 验证成功后返回服务器信息."""
    uuid: str
    token: str
    is_approved: int
    network_dispatch: dict | None = Field(
        None,
        description="探测任务下发信息：{version, targets} 或 null（未批准时）",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 监控数据 (load_now)
# ═══════════════════════════════════════════════════════════════════════════════

class LoadData(BaseModel):
    """单条资源使用快照 — Agent 上报的实时监控数据."""
    cpu: float | None = Field(None, ge=0, le=100, description="CPU 使用率 (%)")
    ram: int | None = Field(None, ge=0, description="已用内存 (Bytes)")
    ram_total: int | None = Field(None, ge=0, description="总内存 (Bytes)")
    swap: int | None = Field(None, ge=0, description="已用 Swap (Bytes)")
    swap_total: int | None = Field(None, ge=0, description="总 Swap (Bytes)")
    load: float | None = Field(None, ge=0, description="系统负载")
    disk: int | None = Field(None, ge=0, description="已用磁盘 (Bytes)")
    disk_total: int | None = Field(None, ge=0, description="总磁盘 (Bytes)")
    net_in: int | None = Field(None, ge=0, description="网络入站速率/流量")
    net_out: int | None = Field(None, ge=0, description="网络出站速率/流量")
    tcp: int | None = Field(None, ge=0, description="TCP 连接数")
    udp: int | None = Field(None, ge=0, description="UDP 连接数")
    process: int | None = Field(None, ge=0, description="运行进程数")


class AgentReportRequest(BaseModel):
    """Agent 混合上报请求 — 同时上报硬件信息和资源使用数据.

    token 用于身份认证；hardware 和 load 可同时或单独上报。
    """
    token: str = Field(..., description="Agent 专属通信 token")

    # ── 硬件信息（可选，首次上报或变更时携带） ──
    name: str | None = Field(None, max_length=128, description="服务器名称")
    cpu_name: str | None = None
    virtualization: str | None = None
    arch: str | None = None
    cpu_cores: int | None = None
    os: str | None = None
    kernel_version: str | None = None
    ipv4: str | None = None
    ipv6: str | None = None
    mem_total: int | None = None
    swap_total: int | None = None
    disk_total: int | None = None
    version: str | None = None

    # ── 系统启动时间（可选） ──
    boot_time: int | None = Field(None, ge=0, description="系统启动时间戳（由 Agent 上报）")

    # ── 累积流量（可选） ──
    total_flow_out: int | None = Field(None, ge=0, description="累积出站流量 (Bytes)")
    total_flow_in: int | None = Field(None, ge=0, description="累积入站流量 (Bytes)")

    # ── 资源使用数据（可选） ──
    load_data: LoadData | None = Field(None, description="实时资源监控数据")

    # ── 网络监控数据（可选） ──
    network_version: str | None = Field(
        None, description="Agent 当前持有的探测目标版本哈希",
    )
    network_data: list[dict] | None = Field(
        None,
        description="探测结果列表: [{target_id, time, median_latency, max_latency, min_latency, packet_loss}]",
    )


class AgentReportResponse(BaseModel):
    """Agent 上报成功响应."""
    uuid: str
    is_approved: int
    received: bool = True
    network_dispatch: dict | None = Field(
        None,
        description="探测任务下发信息：{version, targets} 或 null（无变更）",
    )


class LoadNowRead(BaseModel):
    """返回给前端的监控数据."""
    server_uuid: str
    time: int
    cpu: float | None = None
    ram: int | None = None
    ram_total: int | None = None
    swap: int | None = None
    swap_total: int | None = None
    load: float | None = None
    disk: int | None = None
    disk_total: int | None = None
    net_in: int | None = None
    net_out: int | None = None
    tcp: int | None = None
    udp: int | None = None
    process: int | None = None

    model_config = {"from_attributes": True}

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

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════════════════════
# Group
# ═══════════════════════════════════════════════════════════════════════════════

class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    top: int | None = None


class GroupUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    top: int | None = None


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

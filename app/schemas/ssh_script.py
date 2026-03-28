"""SSH 快捷脚本库的 Pydantic 请求/响应模型."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# 通用响应
# ═══════════════════════════════════════════════════════════════════════════════

class MessageResponse(BaseModel):
    message: str


# ═══════════════════════════════════════════════════════════════════════════════
# SshScript — 请求模型
# ═══════════════════════════════════════════════════════════════════════════════

class SshScriptCreate(BaseModel):
    """创建 SSH 脚本请求."""

    name: str = Field(
        ..., min_length=1, max_length=128,
        description="脚本名称",
    )
    description: str | None = Field(
        None, max_length=512,
        description="脚本的简短描述或用途说明",
    )
    content: str = Field(
        ..., min_length=1, max_length=65535,
        description="具体的脚本代码/命令内容",
    )
    language: str = Field(
        "bash", max_length=20,
        description="脚本语言类型 (bash, python, powershell 等)",
    )


class SshScriptUpdate(BaseModel):
    """更新 SSH 脚本请求（所有字段可选）."""

    name: str | None = Field(
        None, min_length=1, max_length=128,
        description="脚本名称",
    )
    description: str | None = Field(
        None, max_length=512,
        description="脚本的简短描述或用途说明",
    )
    content: str | None = Field(
        None, min_length=1, max_length=65535,
        description="具体的脚本代码/命令内容",
    )
    language: str | None = Field(
        None, max_length=20,
        description="脚本语言类型",
    )
    top: int | None = Field(
        None,
        description="排序权重（值越大越靠前）",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SshScript — 响应模型
# ═══════════════════════════════════════════════════════════════════════════════

class SshScriptRead(BaseModel):
    """SSH 脚本详情."""

    id: int
    name: str
    description: str | None = None
    content: str
    language: str
    top: int
    created_at: int | None = None
    updated_at: int | None = None

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════════════════════
# 批量排序
# ═══════════════════════════════════════════════════════════════════════════════

class SshScriptTopUpdate(BaseModel):
    """批量更新脚本排序值."""

    updates: dict[int, int] = Field(
        ..., min_length=1,
        description="脚本 ID → top 值 的映射",
    )


class SshScriptTopUpdateResponse(BaseModel):
    """批量排序更新结果."""

    total: int
    updated: int
    failed: int
    failed_ids: list[int] = []

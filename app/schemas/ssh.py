"""Web SSH 功能的 Pydantic 请求/响应模型."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# SSH 会话管理
# ═══════════════════════════════════════════════════════════════════════════════

class SSHSessionCreateRequest(BaseModel):
    """创建 SSH 会话请求."""

    username: str = Field(
        "root",
        min_length=1,
        max_length=64,
        description="目标登录用户（证书模式下写入证书 principal）",
    )
    cols: int = Field(80, ge=1, le=500, description="初始终端列数")
    rows: int = Field(24, ge=1, le=200, description="初始终端行数")


class SSHSessionCreateResponse(BaseModel):
    """创建 SSH 会话响应."""

    session_id: str = Field(..., description="会话唯一标识（UUID）")
    ws_url: str = Field(..., description="前端应连接的 WebSocket 地址")


class SSHSessionInfo(BaseModel):
    """单条活跃 SSH 会话信息."""

    session_id: str
    username: str
    connected_at: int
    client_ip: str


class SSHSessionListResponse(BaseModel):
    """活跃 SSH 会话列表."""

    sessions: list[SSHSessionInfo]

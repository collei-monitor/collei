"""Web SFTP 功能的 Pydantic 请求/响应模型."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SFTPSessionCreateRequest(BaseModel):
    """创建 SFTP 会话请求."""

    username: str = Field(
        "root",
        min_length=1,
        max_length=64,
        description="目标登录用户",
    )
    password: str | None = Field(
        None,
        max_length=256,
        description="登录密码（可选，证书模式下不需要）",
    )


class SFTPSessionCreateResponse(BaseModel):
    """创建 SFTP 会话响应."""

    session_id: str = Field(..., description="会话唯一标识（UUID）")
    ws_url: str = Field(..., description="前端应连接的 WebSocket 地址")


class SFTPSessionInfo(BaseModel):
    """单条活跃 SFTP 会话信息."""

    session_id: str
    username: str
    connected_at: int
    client_ip: str


class SFTPSessionListResponse(BaseModel):
    """活跃 SFTP 会话列表."""

    sessions: list[SFTPSessionInfo]

"""系统备份与恢复 Schema."""

from __future__ import annotations

from pydantic import BaseModel


class BackupMeta(BaseModel):
    """备份元数据."""

    version: int
    created_at: int
    collei_version: str
    exclude_monitoring: bool
    files: list[str]


class RestoreResponse(BaseModel):
    """恢复上传响应."""

    message: str
    backup_meta: BackupMeta


class RestoreStatusResponse(BaseModel):
    """恢复状态查询响应."""

    pending: bool
    backup_meta: BackupMeta | None = None


class MessageResponse(BaseModel):
    message: str

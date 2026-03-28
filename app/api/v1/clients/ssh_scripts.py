"""SSH 快捷脚本库管理 API 路由（需管理员登录）.

端点:
  POST   /clients/ssh-scripts                       创建脚本
  GET    /clients/ssh-scripts                       获取脚本列表
  GET    /clients/ssh-scripts/{script_id}           获取单个脚本
  PUT    /clients/ssh-scripts/{script_id}           更新脚本
  DELETE /clients/ssh-scripts/{script_id}           删除脚本
  POST   /clients/ssh-scripts/batch/update-tops     批量更新排序值
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.crud import ssh_script as crud
from app.db.session import get_async_session
from app.models.auth import User
from app.schemas.ssh_script import (
    MessageResponse,
    SshScriptCreate,
    SshScriptRead,
    SshScriptTopUpdate,
    SshScriptTopUpdateResponse,
    SshScriptUpdate,
)

router = APIRouter(prefix="/ssh-scripts", tags=["ssh-scripts"])


# ═══════════════════════════════════════════════════════════════════════════════
# CRUD
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("", response_model=SshScriptRead, status_code=status.HTTP_201_CREATED)
async def create_ssh_script(
    body: SshScriptCreate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """创建一条 SSH 快捷脚本."""
    script = await crud.create_ssh_script(
        db,
        name=body.name,
        content=body.content,
        description=body.description,
        language=body.language,
    )
    return script


@router.get("", response_model=list[SshScriptRead])
async def list_ssh_scripts(
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取所有 SSH 快捷脚本（按 top 降序、创建时间降序）."""
    return await crud.get_all_ssh_scripts(db)


@router.get("/{script_id}", response_model=SshScriptRead)
async def get_ssh_script(
    script_id: int,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取单个 SSH 快捷脚本."""
    script = await crud.get_ssh_script(db, script_id)
    if not script:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Script not found")
    return script


@router.put("/{script_id}", response_model=SshScriptRead)
async def update_ssh_script(
    script_id: int,
    body: SshScriptUpdate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """更新 SSH 快捷脚本."""
    script = await crud.get_ssh_script(db, script_id)
    if not script:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Script not found")

    update_data = body.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "No fields to update",
        )
    updated = await crud.update_ssh_script(db, script_id, **update_data)
    return updated


@router.delete("/{script_id}", response_model=MessageResponse)
async def delete_ssh_script(
    script_id: int,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """删除 SSH 快捷脚本."""
    deleted = await crud.delete_ssh_script(db, script_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Script not found")
    return MessageResponse(message="Script deleted")


# ═══════════════════════════════════════════════════════════════════════════════
# 批量排序
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/batch/update-tops", response_model=SshScriptTopUpdateResponse)
async def batch_update_tops(
    body: SshScriptTopUpdate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """批量更新脚本的排序值."""
    updated, failed, failed_ids = await crud.batch_update_tops(db, body.updates)
    return SshScriptTopUpdateResponse(
        total=len(body.updates),
        updated=updated,
        failed=failed,
        failed_ids=failed_ids,
    )

"""DDNS 任务 API 路由（需管理员登录）.

端点:
  POST    /dns/ddns                     创建 DDNS 任务
  GET     /dns/ddns                     获取所有 DDNS 任务
  GET     /dns/ddns/{id}                获取单个 DDNS 任务
  PUT     /dns/ddns/{id}                更新 DDNS 任务
  DELETE  /dns/ddns/{id}                删除 DDNS 任务
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.crud import dns as crud
from app.db.session import get_async_session
from app.models.auth import User
from app.schemas.dns import (
    DdnsTaskCreate,
    DdnsTaskRead,
    DdnsTaskUpdate,
    MessageResponse,
)

router = APIRouter()


@router.post(
    "/ddns",
    response_model=DdnsTaskRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_ddns_task(
    body: DdnsTaskCreate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """创建 DDNS 任务 — 绑定解析记录到服务器节点."""
    rec = await crud.get_record(db, body.record_id)
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    task = await crud.create_ddns_task(
        db,
        record_id=body.record_id,
        server_uuid=body.server_uuid,
        ip_version=body.ip_version,
    )
    return DdnsTaskRead.from_task(task)


@router.get("/ddns", response_model=list[DdnsTaskRead])
async def list_ddns_tasks(
    active_only: bool = False,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取所有 DDNS 任务."""
    tasks = await crud.get_all_ddns_tasks(db, active_only=active_only)
    return [DdnsTaskRead.from_task(t) for t in tasks]


@router.get("/ddns/{task_id}", response_model=DdnsTaskRead)
async def get_ddns_task(
    task_id: int,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取单个 DDNS 任务."""
    task = await crud.get_ddns_task(db, task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="DDNS task not found")
    return DdnsTaskRead.from_task(task)


@router.put("/ddns/{task_id}", response_model=DdnsTaskRead)
async def update_ddns_task(
    task_id: int,
    body: DdnsTaskUpdate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """更新 DDNS 任务."""
    existing = await crud.get_ddns_task(db, task_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="DDNS task not found")
    updated = await crud.update_ddns_task(
        db, task_id, **body.model_dump(exclude_unset=True))
    return DdnsTaskRead.from_task(updated)


@router.delete("/ddns/{task_id}", response_model=MessageResponse)
async def delete_ddns_task(
    task_id: int,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """删除 DDNS 任务."""
    deleted = await crud.delete_ddns_task(db, task_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="DDNS task not found")
    return MessageResponse(message="DDNS task deleted")

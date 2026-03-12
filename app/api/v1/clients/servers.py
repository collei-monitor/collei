"""服务器管理 API 路由（需管理员登录）.

端点:
  POST    /clients/servers                    管理员创建服务器（被动注册）
  GET     /clients/servers                    获取服务器列表
  GET     /clients/servers/{uuid}             获取单个服务器详情
  PUT     /clients/servers/{uuid}             更新服务器信息
  DELETE  /clients/servers/{uuid}             删除服务器
  POST    /clients/servers/{uuid}/approve     批准服务器
  POST    /clients/servers/{uuid}/token       重新生成通信 token
  POST    /clients/servers/batch/update-tops  批量更新服务器 top 值
  GET     /clients/servers/{uuid}/groups      获取服务器所属分组
  PUT     /clients/servers/{uuid}/groups      设置服务器所属分组
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.v1.clients._helpers import build_server_brief, build_server_full_detail
from app.core.server_cache import server_cache
from app.crud import clients as crud
from app.db.session import get_async_session
from app.models.auth import User
from app.schemas.clients import (
    GroupRead,
    MessageResponse,
    ServerBrief,
    ServerCreate,
    ServerCreateResponse,
    ServerFullDetail,
    ServerGroupSet,
    ServerRead,
    ServerTopUpdate,
    ServerTopUpdateResponse,
    ServerUpdate,
)

router = APIRouter()


@router.post(
    "/servers",
    response_model=ServerCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_server(
    body: ServerCreate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """管理员手动创建服务器记录（被动注册），is_approved 默认为 1."""
    server = await crud.create_server(
        db,
        name=body.name,
        remark=body.remark,
        is_approved=1,
    )
    return ServerCreateResponse(
        uuid=server.uuid,
        name=server.name,
        token=server.token,  # type: ignore[arg-type]
    )


@router.get("/servers", response_model=list[ServerFullDetail])
async def list_servers(
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取所有服务器列表（包含完整详情 + 状态 + 分组）."""
    servers = await crud.get_all_servers(db)
    statuses = {s.uuid: s for s in await crud.get_all_server_statuses(db)}
    result = []
    for srv in servers:
        groups = await crud.get_server_groups(db, srv.uuid)
        st = statuses.get(srv.uuid)
        result.append(build_server_full_detail(srv, st, groups))
    return result


@router.get("/servers/{uuid}", response_model=ServerRead)
async def get_server(
    uuid: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取单个服务器详情."""
    server = await crud.get_server_by_uuid(db, uuid)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    return server


@router.put("/servers/{uuid}", response_model=ServerRead)
async def update_server(
    uuid: str,
    body: ServerUpdate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """更新服务器信息."""
    server = await crud.get_server_by_uuid(db, uuid)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    update_data = body.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )
    updated = await crud.update_server(db, uuid, **update_data)
    server_cache.update_server(uuid, update_data)
    return updated


@router.post("/servers/batch/update-tops", response_model=ServerTopUpdateResponse)
async def batch_update_server_tops(
    body: ServerTopUpdate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """批量更新服务器的 top 值."""
    if not body.updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="updates cannot be empty",
        )

    updated_count, failed_count, failed_uuids = await crud.batch_update_server_tops(
        db, body.updates
    )

    for uuid, top_val in body.updates.items():
        if uuid not in failed_uuids:
            server_cache.update_server(uuid, {"top": top_val})

    return ServerTopUpdateResponse(
        total=len(body.updates),
        updated=updated_count,
        failed=failed_count,
        failed_uuids=failed_uuids,
    )


@router.delete("/servers/{uuid}", response_model=MessageResponse)
async def delete_server(
    uuid: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """删除服务器及其关联数据."""
    deleted = await crud.delete_server(db, uuid)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    server_cache.remove_server(uuid)
    return MessageResponse(message="Server deleted")


@router.post("/servers/{uuid}/approve", response_model=ServerRead)
async def approve_server(
    uuid: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """批准服务器（允许 Agent 上报监控数据）."""
    server = await crud.get_server_by_uuid(db, uuid)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    if server.is_approved == 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Server already approved",
        )
    updated = await crud.approve_server(db, uuid)
    server_cache.update_server(uuid, {
        "uuid": uuid, "name": server.name, "top": server.top,
        "cpu_name": server.cpu_name, "cpu_cores": server.cpu_cores,
        "arch": server.arch, "os": server.os, "region": server.region,
        "mem_total": server.mem_total, "swap_total": server.swap_total,
        "disk_total": server.disk_total, "virtualization": server.virtualization,
        "hidden": server.hidden, "is_approved": 1,
        "created_at": server.created_at,
    })
    return updated


@router.post("/servers/{uuid}/token", response_model=ServerRead)
async def regenerate_token(
    uuid: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """重新生成服务器的通信 token."""
    server = await crud.get_server_by_uuid(db, uuid)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    updated = await crud.regenerate_server_token(db, uuid)
    return updated


# ── 服务器分组关联 ─────────────────────────────────────────────────────────────

@router.get("/servers/{uuid}/groups", response_model=list[GroupRead])
async def get_server_groups(
    uuid: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取服务器所属分组列表."""
    server = await crud.get_server_by_uuid(db, uuid)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    return await crud.get_server_groups(db, uuid)


@router.put("/servers/{uuid}/groups", response_model=list[GroupRead])
async def set_server_groups(
    uuid: str,
    body: ServerGroupSet,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """全量替换服务器所属分组."""
    server = await crud.get_server_by_uuid(db, uuid)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    for gid in body.group_ids:
        if not await crud.get_group_by_id(db, gid):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Group '{gid}' not found",
            )
    groups = await crud.set_server_groups(db, uuid, body.group_ids)
    return groups

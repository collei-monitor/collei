"""分组管理 API 路由（需管理员登录）.

端点:
  POST    /clients/groups                     创建分组（可传入 server_uuids 完成关联）
  GET     /clients/groups                     获取分组列表
  PUT     /clients/groups/{id}                更新分组
  DELETE  /clients/groups/{id}                删除分组
  GET     /clients/groups/{id}/servers        获取分组下的服务器
  POST    /clients/groups/batch/update-tops   批量更新分组 top 值
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.v1.clients._helpers import build_server_brief
from app.crud import clients as crud
from app.db.session import get_async_session
from app.models.auth import User
from app.schemas.clients import (
    GroupCreate,
    GroupRead,
    GroupTopUpdate,
    GroupTopUpdateResponse,
    GroupUpdate,
    MessageResponse,
    ServerBrief,
)

router = APIRouter()


@router.post(
    "/groups",
    response_model=GroupRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_group(
    body: GroupCreate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """创建服务器分组，可在请求体中传入 server_uuids 列表以同时完成关联."""
    existing = await crud.get_group_by_name(db, body.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Group name already exists",
        )
    for uuid in body.server_uuids:
        if not await crud.get_server_by_uuid(db, uuid):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Server '{uuid}' not found",
            )
    group = await crud.create_group(
        db, name=body.name, top=body.top,
        server_uuids=body.server_uuids or None,
    )
    return group


@router.post("/groups/batch/update-tops", response_model=GroupTopUpdateResponse)
async def batch_update_group_tops(
    body: GroupTopUpdate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """批量更新分组的 top 值."""
    if not body.updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="updates cannot be empty",
        )

    updated_count, failed_count, failed_ids = await crud.batch_update_group_tops(
        db, body.updates
    )

    return GroupTopUpdateResponse(
        total=len(body.updates),
        updated=updated_count,
        failed=failed_count,
        failed_ids=failed_ids,
    )


@router.get("/groups", response_model=list[GroupRead])
async def list_groups(
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取所有分组列表."""
    return await crud.get_all_groups(db)


@router.put("/groups/{group_id}", response_model=GroupRead)
async def update_group(
    group_id: str,
    body: GroupUpdate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """更新分组信息，可选择批量更新关联的服务器列表."""
    group = await crud.get_group_by_id(db, group_id)
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    update_data = body.model_dump(exclude_unset=True)
    server_uuids = update_data.pop("server_uuids", None)

    if not update_data and server_uuids is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )

    if "name" in update_data:
        existing = await crud.get_group_by_name(db, update_data["name"])
        if existing and existing.id != group_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Group name already exists",
            )

    if server_uuids is not None:
        for uuid in server_uuids:
            if not await crud.get_server_by_uuid(db, uuid):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Server '{uuid}' not found",
                )
        await crud.set_group_servers(db, group_id, server_uuids)

    if update_data:
        updated = await crud.update_group(db, group_id, **update_data)
    else:
        updated = await crud.get_group_by_id(db, group_id)

    return updated


@router.delete("/groups/{group_id}", response_model=MessageResponse)
async def delete_group(
    group_id: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """删除分组."""
    deleted = await crud.delete_group(db, group_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return MessageResponse(message="Group deleted")


@router.get("/groups/{group_id}/servers", response_model=list[ServerBrief])
async def list_group_servers(
    group_id: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取分组下的所有服务器."""
    group = await crud.get_group_by_id(db, group_id)
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    servers = await crud.get_group_servers(db, group_id)
    statuses = {s.uuid: s for s in await crud.get_all_server_statuses(db)}
    result = []
    for srv in servers:
        groups = await crud.get_server_groups(db, srv.uuid)
        st = statuses.get(srv.uuid)
        result.append(build_server_brief(srv, st, groups))
    return result

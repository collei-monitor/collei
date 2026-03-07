"""客户端与节点管理 API 路由.

端点 — 公开访问（无需登录）:
  GET    /clients/public/servers              获取公开服务器列表（游客过滤 hidden）
  GET    /clients/public/groups               获取分组列表与分组内服务器UUID列表
  GET    /clients/public/servers/{uuid}/load  获取指定服务器的监控数据（游客限制 hidden/is_approved）

端点 — 管理端（需登录）:
  POST   /clients/servers                    管理员创建服务器（被动注册）
  GET    /clients/servers                    获取服务器列表
  GET    /clients/servers/{uuid}             获取单个服务器详情
  PUT    /clients/servers/{uuid}             更新服务器信息
  DELETE /clients/servers/{uuid}             删除服务器
  POST   /clients/servers/{uuid}/approve     批准服务器
  POST   /clients/servers/{uuid}/token       重新生成通信 token
  PUT    /clients/servers/{uuid}/groups      设置服务器所属分组
  GET    /clients/servers/{uuid}/groups      获取服务器所属分组
  POST   /clients/servers/batch/update-tops  批量更新服务器 top 值

  POST   /clients/groups                     创建分组
  GET    /clients/groups                     获取分组列表
  PUT    /clients/groups/{id}                更新分组
  DELETE /clients/groups/{id}                删除分组
  GET    /clients/groups/{id}/servers        获取分组下的服务器

  GET    /clients/servers/{uuid}/load        获取服务器监控数据
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_optional_user
from app.crud import clients as crud
from app.db.session import get_async_session
from app.models.auth import User
from app.crud import monitoring as crud_monitoring
from app.schemas.agent import LoadNowRead
from app.schemas.clients import (
    GroupCreate,
    GroupRead,
    GroupUpdate,
    GroupWithServersRead,
    MessageResponse,
    ServerBrief,
    ServerCreate,
    ServerCreateResponse,
    ServerGroupSet,
    ServerPublicBrief,
    ServerRead,
    ServerStatusRead,
    ServerTopUpdate,
    ServerTopUpdateResponse,
    ServerUpdate,
)

router = APIRouter(prefix="/clients", tags=["clients"])


# ─── 辅助函数 ─────────────────────────────────────────────────────────────────

def _build_server_brief(server, status_obj=None, groups=None) -> ServerBrief:
    """构建服务器简要信息（含状态和分组）."""
    return ServerBrief(
        uuid=server.uuid,
        name=server.name,
        cpu_name=server.cpu_name,
        arch=server.arch,
        os=server.os,
        region=server.region,
        ipv4=server.ipv4,
        ipv6=server.ipv6,
        version=server.version,
        top=server.top,
        hidden=server.hidden,
        is_approved=server.is_approved,
        created_at=server.created_at,
        status=status_obj.status if status_obj else 0,
        last_online=status_obj.last_online if status_obj else None,
        boot_time=status_obj.boot_time if status_obj else None,
        groups=[GroupRead.model_validate(g) for g in groups] if groups else [],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 公开接口（无需认证）
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/public/servers", response_model=list[ServerPublicBrief])
async def list_servers_public(
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_session),
):
    """公开服务器列表。

    - 未登录：仅返回 hidden=0 且 is_approved=1 的服务器，不含敏感字段。
    - 已登录：返回全部服务器（含隐藏），不含敏感字段。
    """
    servers = await crud.get_all_servers(db)
    statuses = {s.uuid: s for s in await crud.get_all_server_statuses(db)}
    result = []
    for srv in servers:
        if current_user is None and (srv.hidden == 1 or srv.is_approved != 1):
            continue
        groups = await crud.get_server_groups(db, srv.uuid)
        st = statuses.get(srv.uuid)
        result.append(ServerPublicBrief(
            uuid=srv.uuid,
            name=srv.name,
            cpu_name=srv.cpu_name,
            arch=srv.arch,
            os=srv.os,
            region=srv.region,
            top=srv.top,
            status=st.status if st else 0,
            last_online=st.last_online if st else None,
            boot_time=st.boot_time if st else None,
            groups=[GroupRead.model_validate(g) for g in groups],
        ))
    return result


@router.get("/public/groups", response_model=list[GroupWithServersRead])
async def list_groups_public(
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_session),
):
    """公开分组列表及其服务器UUID列表。

    - 未登录：仅返回分组内 hidden=0 且 is_approved=1 的服务器UUID。
    - 已登录：返回分组内的全部服务器UUID。
    """
    groups = await crud.get_all_groups(db)
    result = []
    
    for group in groups:
        servers = await crud.get_group_servers(db, group.id)
        server_uuids = []
        
        for srv in servers:
            # 过滤逻辑：未登录用户只看公开服务器
            if current_user is None and (srv.hidden == 1 or srv.is_approved != 1):
                continue
            server_uuids.append(srv.uuid)
        
        result.append(GroupWithServersRead(
            id=group.id,
            name=group.name,
            top=group.top,
            created_at=group.created_at,
            server_uuids=server_uuids,
        ))
    
    return result


@router.get("/public/servers/{uuid}/load", response_model=list[LoadNowRead])
async def get_server_load_public(
    uuid: str,
    range: int | None = Query(default=None, description="查询范围（小时），不传则返回 load_now 最新数据"),
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_session),
):
    """公开获取指定服务器的监控数据。

    - 未登录：仅允许查询 hidden=0 且 is_approved=1 的服务器。
    - 已登录：可查询任意服务器。

    查询方式：
      - 不传 range：返回 load_now 最新数据（约 1 分钟粒度）。
      - 传入 range（小时）：查询过去 N 小时的数据，后续可根据 range 大小路由至不同精度的表。
    """
    server = await crud.get_server_by_uuid(db, uuid)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    if current_user is None and (server.hidden == 1 or server.is_approved != 1):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    if range is None:
        records = await crud_monitoring.get_load_now(db, uuid)
    else:
        now = int(time.time())
        records = await crud_monitoring.get_load_range(
            db, uuid, start_time=now - range * 3600, end_time=now,
        )
    return records


# ═══════════════════════════════════════════════════════════════════════════════
# Server 管理（需认证）
# ═══════════════════════════════════════════════════════════════════════════════

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
        install_hint=f"install.sh --url=<YOUR_API_URL> --token={server.token}",
    )


@router.get("/servers", response_model=list[ServerBrief])
async def list_servers(
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取所有服务器列表（含状态 & 分组）."""
    servers = await crud.get_all_servers(db)
    statuses = {s.uuid: s for s in await crud.get_all_server_statuses(db)}
    result = []
    for srv in servers:
        groups = await crud.get_server_groups(db, srv.uuid)
        st = statuses.get(srv.uuid)
        result.append(_build_server_brief(srv, st, groups))
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
    return updated


@router.post("/servers/batch/update-tops", response_model=ServerTopUpdateResponse)
async def batch_update_server_tops(
    body: ServerTopUpdate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """批量更新服务器的 top 值.

    请求体格式:
    ```json
    {
        "updates": {
            "uuid-1": 100,
            "uuid-2": 200,
            "uuid-3": 50
        }
    }
    ```
    """
    if not body.updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="updates cannot be empty",
        )

    updated_count, failed_count, failed_uuids = await crud.batch_update_server_tops(
        db, body.updates
    )

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


# ── 服务器状态 ─────────────────────────────────────────────────────────────────

@router.get("/servers/{uuid}/status", response_model=ServerStatusRead)
async def get_server_status(
    uuid: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取服务器当前状态."""
    server = await crud.get_server_by_uuid(db, uuid)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    ss = await crud.get_server_status(db, uuid)
    if not ss:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server status not found",
        )
    return ss


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
    # 验证分组都存在
    for gid in body.group_ids:
        if not await crud.get_group_by_id(db, gid):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Group '{gid}' not found",
            )
    groups = await crud.set_server_groups(db, uuid, body.group_ids)
    return groups


# ═══════════════════════════════════════════════════════════════════════════════
# Group 管理（需认证）
# ═══════════════════════════════════════════════════════════════════════════════

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
    """创建服务器分组."""
    existing = await crud.get_group_by_name(db, body.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Group name already exists",
        )
    group = await crud.create_group(db, name=body.name, top=body.top)
    return group


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
    """更新分组信息."""
    group = await crud.get_group_by_id(db, group_id)
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    update_data = body.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )
    # 名称唯一性检查
    if "name" in update_data:
        existing = await crud.get_group_by_name(db, update_data["name"])
        if existing and existing.id != group_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Group name already exists",
            )
    updated = await crud.update_group(db, group_id, **update_data)
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
        result.append(_build_server_brief(srv, st, groups))
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 监控数据查询（需认证）
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/servers/{uuid}/load", response_model=list[LoadNowRead])
async def get_server_load(
    uuid: str,
    limit: int = 60,
    start_time: int | None = None,
    end_time: int | None = None,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取服务器监控数据.

    支持两种查询方式:
      - 默认: 最近 N 条（limit 参数，默认 60）
      - 时间范围: 指定 start_time 和 end_time
    """
    server = await crud.get_server_by_uuid(db, uuid)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    if start_time is not None and end_time is not None:
        records = await crud_monitoring.get_load_range(
            db, uuid, start_time=start_time, end_time=end_time,
        )
    else:
        records = await crud_monitoring.get_load_now(db, uuid, limit=limit)
    return records

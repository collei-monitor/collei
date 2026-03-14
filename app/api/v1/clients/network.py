"""网络监控管理 API 路由（需管理员登录）.

端点:
  POST   /clients/network/targets                 创建监控目标
  GET    /clients/network/targets                 获取所有监控目标
  GET    /clients/network/targets/{id}            获取目标详情（含下发节点）
  PUT    /clients/network/targets/{id}            更新监控目标
  DELETE /clients/network/targets/{id}            删除监控目标
  PUT    /clients/network/targets/{id}/dispatch   设置目标下发节点
  GET    /clients/network/status/{id}             获取目标探测结果
  GET    /clients/network/status/{id}/latest      获取目标各节点最新结果
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.server_cache import server_cache
from app.crud import network as crud_net
from app.db.session import get_async_session
from app.models.auth import User
from app.schemas.network import (
    DispatchRead,
    DispatchSetRequest,
    NetworkStatusLatest,
    NetworkStatusRead,
    NetworkTargetCreate,
    NetworkTargetDetail,
    NetworkTargetRead,
    NetworkTargetUpdate,
)

router = APIRouter(prefix="/network", tags=["network"])

# 存储当前版本哈希，供 Agent 增量拉取
_dispatch_version: str = ""


def _invalidate_dispatch_version() -> None:
    """标记 dispatch 版本失效，下次 Agent 拉取时重新计算."""
    global _dispatch_version
    _dispatch_version = ""


async def get_current_dispatch_version(db: AsyncSession) -> str:
    """获取当前 dispatch 版本号（懒计算）."""
    global _dispatch_version
    if not _dispatch_version:
        targets = await crud_net.get_all_targets(db, enabled_only=True)
        _dispatch_version = crud_net.compute_dispatch_hash(targets)
    return _dispatch_version


# ═══════════════════════════════════════════════════════════════════════════════
# 监控目标 CRUD
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/targets", response_model=NetworkTargetRead, status_code=201)
async def create_target(
    body: NetworkTargetCreate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """创建网络监控目标."""
    target = await crud_net.create_target(
        db, **body.model_dump(),
    )
    _invalidate_dispatch_version()
    return target


@router.get("/targets", response_model=list[NetworkTargetRead])
async def list_targets(
    enabled_only: bool = False,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取所有监控目标列表."""
    return await crud_net.get_all_targets(db, enabled_only=enabled_only)


@router.get("/targets/{target_id}", response_model=NetworkTargetDetail)
async def get_target_detail(
    target_id: int,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取目标详情（含下发节点配置）."""
    target = await crud_net.get_target(db, target_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target not found")
    dispatches = await crud_net.get_dispatches_by_target(db, target_id)
    return NetworkTargetDetail(
        target=NetworkTargetRead.model_validate(target),
        dispatches=[DispatchRead.model_validate(d) for d in dispatches],
    )


@router.put("/targets/{target_id}", response_model=NetworkTargetRead)
async def update_target(
    target_id: int,
    body: NetworkTargetUpdate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """更新监控目标."""
    existing = await crud_net.get_target(db, target_id)
    if not existing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target not found")
    updated = await crud_net.update_target(
        db, target_id, **body.model_dump(exclude_none=True),
    )
    _invalidate_dispatch_version()
    return updated


@router.delete("/targets/{target_id}")
async def delete_target(
    target_id: int,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """删除监控目标（级联删除下发节点和探测结果）."""
    deleted = await crud_net.delete_target(db, target_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target not found")
    _invalidate_dispatch_version()
    return {"message": "Deleted"}


# ═══════════════════════════════════════════════════════════════════════════════
# 下发节点配置
# ═══════════════════════════════════════════════════════════════════════════════

@router.put(
    "/targets/{target_id}/dispatch",
    response_model=list[DispatchRead],
)
async def set_target_dispatch(
    target_id: int,
    body: DispatchSetRequest,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """全量设置目标的下发节点."""
    target = await crud_net.get_target(db, target_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target not found")
    dispatches = await crud_net.set_dispatches_for_target(
        db, target_id,
        [d.model_dump() for d in body.dispatches],
    )
    _invalidate_dispatch_version()
    return dispatches


# ═══════════════════════════════════════════════════════════════════════════════
# 探测结果查询
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/status/{target_id}",
    response_model=list[NetworkStatusRead],
)
async def get_target_status(
    target_id: int,
    limit: int = Query(100, ge=1, le=1000),
    start_time: int | None = None,
    end_time: int | None = None,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取目标的探测结果历史."""
    target = await crud_net.get_target(db, target_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target not found")
    return await crud_net.get_network_status_by_target(
        db, target_id,
        limit=limit, start_time=start_time, end_time=end_time,
    )


@router.get(
    "/status/{target_id}/latest",
    response_model=list[NetworkStatusLatest],
)
async def get_target_latest_status(
    target_id: int,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取目标下每个节点的最新探测结果."""
    target = await crud_net.get_target(db, target_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target not found")
    records = await crud_net.get_latest_status_per_server(db, target_id)
    result = []
    for r in records:
        srv = server_cache._servers.get(r.server_uuid)
        result.append(NetworkStatusLatest(
            target_id=r.target_id,
            server_uuid=r.server_uuid,
            server_name=srv.get("name") if srv else None,
            time=r.time,
            median_latency=r.median_latency,
            max_latency=r.max_latency,
            min_latency=r.min_latency,
            packet_loss=r.packet_loss,
        ))
    return result

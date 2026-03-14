"""网络监控目标与探测结果的 CRUD / DAO 操作."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.network import NetworkStatus, NetworkTarget, NetworkTargetDispatch


# ═══════════════════════════════════════════════════════════════════════════════
# NetworkTarget — 监控目标管理
# ═══════════════════════════════════════════════════════════════════════════════

async def create_target(
    db: AsyncSession,
    *,
    name: str,
    host: str,
    protocol: str = "icmp",
    port: int | None = None,
    interval: int = 60,
    enabled: int = 1,
) -> NetworkTarget:
    """创建网络监控目标."""
    target = NetworkTarget(
        name=name, host=host, protocol=protocol,
        port=port, interval=interval, enabled=enabled,
    )
    db.add(target)
    await db.flush()
    return target


async def get_target(db: AsyncSession, target_id: int) -> NetworkTarget | None:
    """根据 ID 获取单个监控目标."""
    result = await db.execute(
        select(NetworkTarget).where(NetworkTarget.id == target_id),
    )
    return result.scalar_one_or_none()


async def get_all_targets(
    db: AsyncSession,
    *,
    enabled_only: bool = False,
) -> Sequence[NetworkTarget]:
    """获取所有监控目标."""
    stmt = select(NetworkTarget).order_by(NetworkTarget.id.asc())
    if enabled_only:
        stmt = stmt.where(NetworkTarget.enabled == 1)
    result = await db.execute(stmt)
    return result.scalars().all()


async def update_target(
    db: AsyncSession,
    target_id: int,
    **kwargs: Any,
) -> NetworkTarget | None:
    """更新监控目标字段."""
    allowed = {"name", "host", "protocol", "port", "interval", "enabled"}
    values = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not values:
        return await get_target(db, target_id)
    await db.execute(
        update(NetworkTarget).where(NetworkTarget.id == target_id).values(**values),
    )
    await db.flush()
    return await get_target(db, target_id)


async def delete_target(db: AsyncSession, target_id: int) -> bool:
    """删除监控目标（级联删除 dispatch 和 status）."""
    result = await db.execute(
        delete(NetworkTarget).where(NetworkTarget.id == target_id),
    )
    await db.flush()
    return (result.rowcount or 0) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# NetworkTargetDispatch — 下发节点管理
# ═══════════════════════════════════════════════════════════════════════════════

async def get_dispatches_by_target(
    db: AsyncSession,
    target_id: int,
) -> Sequence[NetworkTargetDispatch]:
    """获取某个目标的所有下发节点."""
    result = await db.execute(
        select(NetworkTargetDispatch)
        .where(NetworkTargetDispatch.target_id == target_id)
        .order_by(NetworkTargetDispatch.node_type, NetworkTargetDispatch.node_id),
    )
    return result.scalars().all()


async def set_dispatches_for_target(
    db: AsyncSession,
    target_id: int,
    dispatches: list[dict[str, Any]],
) -> Sequence[NetworkTargetDispatch]:
    """全量替换某个目标的下发节点列表.

    Args:
        dispatches: [{"node_type": "server"|"global", "node_id": "uuid"|"all", "is_exclude": 0|1}, ...]
    """
    await db.execute(
        delete(NetworkTargetDispatch)
        .where(NetworkTargetDispatch.target_id == target_id),
    )
    for d in dispatches:
        db.add(NetworkTargetDispatch(
            target_id=target_id,
            node_type=d["node_type"],
            node_id=d["node_id"],
            is_exclude=d.get("is_exclude", 0),
        ))
    await db.flush()
    return await get_dispatches_by_target(db, target_id)


async def get_dispatch_targets_for_server(
    db: AsyncSession,
    server_uuid: str,
) -> Sequence[NetworkTarget]:
    """获取某个 Agent 节点应执行的所有监控目标.

    匹配逻辑：
      1. node_type='server', node_id=server_uuid, is_exclude=0
      2. node_type='global', node_id='all', is_exclude=0
         且该 target 没有 node_type='global', node_id=server_uuid, is_exclude=1
    """
    # 直接指定的目标
    direct_stmt = (
        select(NetworkTargetDispatch.target_id)
        .where(
            NetworkTargetDispatch.node_type == "server",
            NetworkTargetDispatch.node_id == server_uuid,
            NetworkTargetDispatch.is_exclude == 0,
        )
    )

    # 全局目标（排除此节点被 exclude 的）
    excluded_stmt = (
        select(NetworkTargetDispatch.target_id)
        .where(
            NetworkTargetDispatch.node_type == "global",
            NetworkTargetDispatch.node_id == server_uuid,
            NetworkTargetDispatch.is_exclude == 1,
        )
    )
    global_stmt = (
        select(NetworkTargetDispatch.target_id)
        .where(
            NetworkTargetDispatch.node_type == "global",
            NetworkTargetDispatch.node_id == "all",
            NetworkTargetDispatch.is_exclude == 0,
        )
        .where(NetworkTargetDispatch.target_id.not_in(excluded_stmt))
    )

    combined_ids = direct_stmt.union(global_stmt)

    stmt = (
        select(NetworkTarget)
        .where(
            NetworkTarget.id.in_(combined_ids),
            NetworkTarget.enabled == 1,
        )
        .order_by(NetworkTarget.id.asc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


# ═══════════════════════════════════════════════════════════════════════════════
# NetworkStatus — 探测结果
# ═══════════════════════════════════════════════════════════════════════════════

async def insert_network_status(
    db: AsyncSession,
    *,
    target_id: int,
    server_uuid: str,
    ts: int | None = None,
    median_latency: float | None = None,
    max_latency: float | None = None,
    min_latency: float | None = None,
    packet_loss: float = 0,
) -> NetworkStatus:
    """写入一条探测结果."""
    ts = ts or int(time.time())
    record = NetworkStatus(
        target_id=target_id,
        server_uuid=server_uuid,
        time=ts,
        median_latency=median_latency,
        max_latency=max_latency,
        min_latency=min_latency,
        packet_loss=packet_loss,
    )
    db.add(record)
    await db.flush()
    return record


async def batch_insert_network_status(
    db: AsyncSession,
    records: list[dict[str, Any]],
    server_uuid: str,
) -> int:
    """批量写入探测结果.

    Args:
        records: [{"target_id": int, "time": int, "median_latency": ..., ...}, ...]
        server_uuid: 执行探测的节点 UUID

    Returns:
        成功插入的记录数
    """
    allowed_keys = {"target_id", "time", "median_latency", "max_latency", "min_latency", "packet_loss"}
    count = 0
    for r in records:
        values = {k: v for k, v in r.items() if k in allowed_keys}
        if "target_id" not in values:
            continue
        values.setdefault("time", int(time.time()))
        values.setdefault("packet_loss", 0)
        db.add(NetworkStatus(server_uuid=server_uuid, **values))
        count += 1
    if count:
        await db.flush()
    return count


async def get_network_status_by_target(
    db: AsyncSession,
    target_id: int,
    *,
    limit: int = 100,
    start_time: int | None = None,
    end_time: int | None = None,
) -> Sequence[NetworkStatus]:
    """获取某个目标的探测结果."""
    stmt = select(NetworkStatus).where(NetworkStatus.target_id == target_id)
    if start_time is not None:
        stmt = stmt.where(NetworkStatus.time >= start_time)
    if end_time is not None:
        stmt = stmt.where(NetworkStatus.time <= end_time)
    stmt = stmt.order_by(NetworkStatus.time.desc()).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_network_status_by_server(
    db: AsyncSession,
    server_uuid: str,
    *,
    target_id: int | None = None,
    limit: int = 100,
    start_time: int | None = None,
    end_time: int | None = None,
) -> Sequence[NetworkStatus]:
    """获取某个节点的探测结果."""
    stmt = select(NetworkStatus).where(NetworkStatus.server_uuid == server_uuid)
    if target_id is not None:
        stmt = stmt.where(NetworkStatus.target_id == target_id)
    if start_time is not None:
        stmt = stmt.where(NetworkStatus.time >= start_time)
    if end_time is not None:
        stmt = stmt.where(NetworkStatus.time <= end_time)
    stmt = stmt.order_by(NetworkStatus.time.desc()).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_latest_status_per_server(
    db: AsyncSession,
    target_id: int,
) -> Sequence[NetworkStatus]:
    """获取某个目标下每个节点的最新探测结果（用于面板展示）."""
    from sqlalchemy import func

    latest_sub = (
        select(
            NetworkStatus.server_uuid,
            func.max(NetworkStatus.time).label("max_time"),
        )
        .where(NetworkStatus.target_id == target_id)
        .group_by(NetworkStatus.server_uuid)
        .subquery()
    )
    stmt = select(NetworkStatus).join(
        latest_sub,
        (NetworkStatus.server_uuid == latest_sub.c.server_uuid)
        & (NetworkStatus.time == latest_sub.c.max_time)
        & (NetworkStatus.target_id == target_id),
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def purge_old_network_status(
    db: AsyncSession,
    *,
    before: int,
    target_id: int | None = None,
) -> int:
    """清理指定时间之前的探测结果."""
    stmt = delete(NetworkStatus).where(NetworkStatus.time < before)
    if target_id is not None:
        stmt = stmt.where(NetworkStatus.target_id == target_id)
    result = await db.execute(stmt)
    await db.flush()
    return result.rowcount or 0


# ═══════════════════════════════════════════════════════════════════════════════
# 版本号/哈希 — 增量下发支持
# ═══════════════════════════════════════════════════════════════════════════════

def compute_dispatch_hash(targets: Sequence[NetworkTarget]) -> str:
    """根据目标列表计算哈希摘要，用于 Agent 增量拉取判断.

    Agent 携带上次收到的 hash，后端比对决定是否返回更新的目标列表。
    """
    data = [
        {
            "id": t.id,
            "name": t.name,
            "host": t.host,
            "protocol": t.protocol,
            "port": t.port,
            "interval": t.interval,
        }
        for t in targets
    ]
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

"""系统资源监控数据的 CRUD / DAO 操作."""

from __future__ import annotations

import time
from typing import Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.monitoring import LoadNow, TrafficHourlyStat


# ═══════════════════════════════════════════════════════════════════════════════
# LoadNow — 实时监控数据
# ═══════════════════════════════════════════════════════════════════════════════

async def insert_load(
    db: AsyncSession,
    *,
    server_uuid: str,
    data: dict,
    ts: int | None = None,
) -> LoadNow:
    """写入一条监控数据记录.

    Args:
        server_uuid: 服务器 UUID
        data: 监控数据字段字典（cpu, ram, ...）
        ts: 记录时间戳，默认当前时间
    """
    ts = ts or int(time.time())
    allowed_keys = {
        "cpu", "ram", "ram_total", "swap", "swap_total", "load",
        "disk", "disk_total", "net_in", "net_out", "tcp", "udp", "process",
    }
    values = {k: v for k, v in data.items() if k in allowed_keys and v is not None}

    record = LoadNow(server_uuid=server_uuid, time=ts, **values)
    db.add(record)
    await db.flush()
    return record


async def get_load_now(
    db: AsyncSession,
    server_uuid: str,
    *,
    limit: int = 60,
) -> Sequence[LoadNow]:
    """获取服务器最近的监控数据（默认 60 条，约 1 分钟）.

    按时间降序返回。
    """
    stmt = (
        select(LoadNow)
        .where(LoadNow.server_uuid == server_uuid)
        .order_by(LoadNow.time.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_load_range(
    db: AsyncSession,
    server_uuid: str,
    *,
    start_time: int,
    end_time: int,
) -> Sequence[LoadNow]:
    """获取指定时间范围内的监控数据."""
    stmt = (
        select(LoadNow)
        .where(
            LoadNow.server_uuid == server_uuid,
            LoadNow.time >= start_time,
            LoadNow.time <= end_time,
        )
        .order_by(LoadNow.time.asc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_latest_load(
    db: AsyncSession,
    server_uuid: str,
) -> LoadNow | None:
    """获取服务器最新一条监控数据."""
    stmt = (
        select(LoadNow)
        .where(LoadNow.server_uuid == server_uuid)
        .order_by(LoadNow.time.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def purge_old_load(
    db: AsyncSession,
    server_uuid: str,
    *,
    before: int,
) -> int:
    """清理指定时间之前的监控数据.

    Args:
        server_uuid: 服务器 UUID
        before: 清除此时间戳之前的数据

    Returns:
        删除的记录数
    """
    result = await db.execute(
        delete(LoadNow).where(
            LoadNow.server_uuid == server_uuid,
            LoadNow.time < before,
        )
    )
    return result.rowcount or 0


async def purge_all_load(
    db: AsyncSession,
    server_uuid: str,
) -> int:
    """清除服务器的所有监控数据."""
    result = await db.execute(
        delete(LoadNow).where(LoadNow.server_uuid == server_uuid)
    )
    return result.rowcount or 0


# ═══════════════════════════════════════════════════════════════════════════════
# TrafficHourlyStat — 每小时流量统计
# ═══════════════════════════════════════════════════════════════════════════════

def _floor_to_hour(ts: int) -> int:
    """将时间戳向下取整到当前小时的 00:00."""
    return ts - (ts % 3600)


async def upsert_traffic_hourly(
    db: AsyncSession,
    *,
    server_uuid: str,
    net_in: int,
    net_out: int,
    ts: int | None = None,
) -> TrafficHourlyStat:
    """写入或累加流量统计记录.

    如果该小时的记录不存在，则直接 INSERT；
    如果已存在，则将原有的 net_in/net_out 加上本次上报的增量。
    """
    ts = ts or int(time.time())
    hour_ts = _floor_to_hour(ts)

    existing = await db.execute(
        select(TrafficHourlyStat).where(
            TrafficHourlyStat.server_uuid == server_uuid,
            TrafficHourlyStat.time == hour_ts,
        )
    )
    record = existing.scalar_one_or_none()

    if record:
        await db.execute(
            update(TrafficHourlyStat)
            .where(
                TrafficHourlyStat.server_uuid == server_uuid,
                TrafficHourlyStat.time == hour_ts,
            )
            .values(
                net_in=TrafficHourlyStat.net_in + net_in,
                net_out=TrafficHourlyStat.net_out + net_out,
            )
        )
        await db.flush()
        result = await db.execute(
            select(TrafficHourlyStat).where(
                TrafficHourlyStat.server_uuid == server_uuid,
                TrafficHourlyStat.time == hour_ts,
            )
        )
        return result.scalar_one()  # type: ignore[return-value]

    new_record = TrafficHourlyStat(
        server_uuid=server_uuid,
        time=hour_ts,
        net_in=net_in,
        net_out=net_out,
    )
    db.add(new_record)
    await db.flush()
    return new_record


async def get_traffic_hourly_range(
    db: AsyncSession,
    server_uuid: str,
    *,
    start_time: int,
    end_time: int,
) -> Sequence[TrafficHourlyStat]:
    """获取指定时间范围内的小时流量统计."""
    stmt = (
        select(TrafficHourlyStat)
        .where(
            TrafficHourlyStat.server_uuid == server_uuid,
            TrafficHourlyStat.time >= start_time,
            TrafficHourlyStat.time <= end_time,
        )
        .order_by(TrafficHourlyStat.time.asc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def purge_old_traffic_hourly(
    db: AsyncSession,
    server_uuid: str,
    *,
    before: int,
) -> int:
    """清理指定时间之前的流量统计数据."""
    result = await db.execute(
        delete(TrafficHourlyStat).where(
            TrafficHourlyStat.server_uuid == server_uuid,
            TrafficHourlyStat.time < before,
        )
    )
    return result.rowcount or 0

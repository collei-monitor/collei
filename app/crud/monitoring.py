"""系统资源监控数据的 CRUD / DAO 操作."""

from __future__ import annotations

import calendar
import time
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import delete, func, select, update
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


# ═══════════════════════════════════════════════════════════════════════════════
# 周期流量计算
# ═══════════════════════════════════════════════════════════════════════════════

def get_cycle_start_ts(traffic_reset_day: int, billing_cycle_data: int | None = None) -> int:
    """根据流量重置日计算当前周期的起始时间戳.

    Args:
        traffic_reset_day: 流量重置日 (0=不重置, -1=每月最后一天, 1-31=指定日)
        billing_cycle_data: 计费周期日（当 traffic_reset_day 为 None 时回退使用）
    """
    effective_day = traffic_reset_day
    if effective_day is None:
        effective_day = billing_cycle_data if billing_cycle_data else 0
    if effective_day == 0:
        return 0  # 不重置

    now = datetime.now(timezone.utc)

    if effective_day == -1:
        last_day = calendar.monthrange(now.year, now.month)[1]
        if now.day >= last_day:
            cycle_start = datetime(now.year, now.month, last_day, 0, 0, 0, tzinfo=timezone.utc)
        else:
            prev_year, prev_month = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
            prev_last = calendar.monthrange(prev_year, prev_month)[1]
            cycle_start = datetime(prev_year, prev_month, prev_last, 0, 0, 0, tzinfo=timezone.utc)
    else:
        reset_day = min(effective_day, 28)  # 安全范围
        if now.day >= reset_day:
            try:
                actual_day = min(reset_day, calendar.monthrange(now.year, now.month)[1])
                cycle_start = datetime(now.year, now.month, actual_day, 0, 0, 0, tzinfo=timezone.utc)
            except ValueError:
                cycle_start = datetime(now.year, now.month, 1, 0, 0, 0, tzinfo=timezone.utc)
        else:
            prev_year, prev_month = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
            try:
                actual_day = min(reset_day, calendar.monthrange(prev_year, prev_month)[1])
                cycle_start = datetime(prev_year, prev_month, actual_day, 0, 0, 0, tzinfo=timezone.utc)
            except ValueError:
                cycle_start = datetime(prev_year, prev_month, 1, 0, 0, 0, tzinfo=timezone.utc)

    return int(cycle_start.timestamp())


def calc_traffic_used(net_in_total: int, net_out_total: int, accounting_mode: int | None) -> int:
    """根据流量计算模式计算已用流量.

    Args:
        net_in_total: 周期内入站流量总和
        net_out_total: 周期内出站流量总和
        accounting_mode: 1-仅出站 2-仅入站 3-总和 4-取最大 5-取最小
    """
    mode = accounting_mode or 1
    if mode == 1:
        return net_out_total
    elif mode == 2:
        return net_in_total
    elif mode == 3:
        return net_in_total + net_out_total
    elif mode == 4:
        return max(net_in_total, net_out_total)
    elif mode == 5:
        return min(net_in_total, net_out_total)
    return net_out_total


async def get_cycle_traffic(
    db: AsyncSession,
    server_uuid: str,
    *,
    traffic_reset_day: int,
    billing_cycle_data: int | None = None,
    accounting_mode: int | None = None,
) -> int:
    """计算服务器当前周期的已用流量."""
    cycle_start = get_cycle_start_ts(traffic_reset_day, billing_cycle_data)
    if cycle_start == 0:
        return 0

    stmt = select(
        func.coalesce(func.sum(TrafficHourlyStat.net_in), 0).label("total_in"),
        func.coalesce(func.sum(TrafficHourlyStat.net_out), 0).label("total_out"),
    ).where(
        TrafficHourlyStat.server_uuid == server_uuid,
        TrafficHourlyStat.time >= cycle_start,
    )
    row = (await db.execute(stmt)).one()
    return calc_traffic_used(row.total_in, row.total_out, accounting_mode)


async def batch_get_cycle_traffic(
    db: AsyncSession,
    billing_rules: list[dict],
) -> dict[str, int]:
    """批量计算多台服务器的周期流量.

    Args:
        billing_rules: [{"uuid": ..., "traffic_reset_day": ..., "billing_cycle_data": ..., "accounting_mode": ...}]

    Returns:
        {uuid: traffic_used}
    """
    result: dict[str, int] = {}
    # 按 cycle_start 分组查询以减少 DB 往返
    groups: dict[int, list[dict]] = {}
    for rule in billing_rules:
        reset_day = rule.get("traffic_reset_day")
        if reset_day is None:
            reset_day = rule.get("billing_cycle_data", 0)
        if reset_day == 0:
            result[rule["uuid"]] = 0
            continue
        cs = get_cycle_start_ts(reset_day, rule.get("billing_cycle_data"))
        if cs == 0:
            result[rule["uuid"]] = 0
            continue
        groups.setdefault(cs, []).append(rule)

    for cycle_start, rules in groups.items():
        uuids = [r["uuid"] for r in rules]
        stmt = select(
            TrafficHourlyStat.server_uuid,
            func.coalesce(func.sum(TrafficHourlyStat.net_in), 0).label("total_in"),
            func.coalesce(func.sum(TrafficHourlyStat.net_out), 0).label("total_out"),
        ).where(
            TrafficHourlyStat.server_uuid.in_(uuids),
            TrafficHourlyStat.time >= cycle_start,
        ).group_by(TrafficHourlyStat.server_uuid)

        rows = (await db.execute(stmt)).all()
        traffic_map = {row.server_uuid: (row.total_in, row.total_out) for row in rows}
        for rule in rules:
            uuid = rule["uuid"]
            in_total, out_total = traffic_map.get(uuid, (0, 0))
            result[uuid] = calc_traffic_used(in_total, out_total, rule.get("accounting_mode"))

    return result

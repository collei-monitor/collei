"""后台任务模块.

管理所有定时任务和后台作业:
  1. 离线检测 — 定期扫描超时未上报的服务器并标记为离线
  2. 广播快照 — 定期将服务器状态 + 最新监控数据推送给 WebSocket 客户端
  3. 数据清理 — 定期清除过期的 load_now 监控记录
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from sqlalchemy import select, update, delete

from app.core.config import settings
from app.db.session import async_session_factory
from app.models.clients import Server, ServerStatus
from app.models.monitoring import LoadNow


class BackgroundTasks:
    """后台任务管理器."""

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """启动所有后台任务."""
        self._tasks.append(asyncio.create_task(self._check_offline_servers()))
        self._tasks.append(asyncio.create_task(self._broadcast_snapshot()))
        self._tasks.append(asyncio.create_task(self._purge_old_load()))
        print("✅ 后台任务已启动")

    async def stop(self) -> None:
        """停止所有后台任务."""
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        print("ℹ️ 后台任务已停止")

    # ─────────────────────────────────────────────────────────────────────────
    # Task 1: 离线检测
    # ─────────────────────────────────────────────────────────────────────────

    async def _check_offline_servers(self) -> None:
        """定期检查并标记离线服务器.

        规则: server_status.status == 1 (在线)
              且 last_online < now - OFFLINE_THRESHOLD_SECONDS
              → 将 status 更新为 0 (离线)
        """
        while True:
            try:
                threshold = int(time.time()) - \
                    settings.OFFLINE_THRESHOLD_SECONDS

                async with async_session_factory() as session:
                    # 查出需要标记离线的 uuid
                    stmt = (
                        select(ServerStatus.uuid)
                        .where(
                            ServerStatus.status == 1,
                            ServerStatus.last_online < threshold,
                        )
                    )
                    result = await session.execute(stmt)
                    offline_uuids: list[str] = list(result.scalars().all())

                    if offline_uuids:
                        await session.execute(
                            update(ServerStatus)
                            .where(ServerStatus.uuid.in_(offline_uuids))
                            .values(status=0)
                        )
                        await session.commit()
                        print(
                            f"⚠️ 检测到 {len(offline_uuids)} 台服务器离线: "
                            f"{offline_uuids}"
                        )

            except Exception as e:
                print(f"⚠️ 离线检测任务出错: {e}")

            await asyncio.sleep(settings.OFFLINE_CHECK_INTERVAL)

    # ─────────────────────────────────────────────────────────────────────────
    # Task 2: WebSocket 广播
    # ─────────────────────────────────────────────────────────────────────────

    async def _broadcast_snapshot(self) -> None:
        """定期构建服务器快照并广播给所有 WebSocket 客户端.

        快照格式:
        {
            "type": "snapshot",
            "timestamp": <unix_ts>,
            "servers": [
                {
                    "uuid": "...",
                    "name": "...",
                    "status": 1,
                    "last_online": 1234567890,
                    "load": { ... }   # 最新的 load_now 记录
                },
                ...
            ]
        }
        """
        from app.core.ws_manager import ws_manager

        while True:
            try:
                if ws_manager.has_connections:
                    snapshot = await self._build_snapshot()
                    await ws_manager.broadcast(snapshot)

            except Exception as e:
                print(f"⚠️ 广播快照出错: {e}")

            await asyncio.sleep(settings.WS_BROADCAST_INTERVAL)

    async def _build_snapshot(self) -> dict[str, Any]:
        """从数据库构建完整的服务器状态快照."""
        now = int(time.time())

        async with async_session_factory() as session:
            # 一次性查出所有已批准的服务器
            server_stmt = (
                select(Server)
                .where(Server.is_approved == 1, Server.hidden == 0)
                .order_by(Server.top.desc(), Server.created_at.desc())
            )
            servers = (await session.execute(server_stmt)).scalars().all()

            if not servers:
                return {"type": "snapshot", "timestamp": now, "servers": []}

            uuids = [s.uuid for s in servers]

            # 批量查状态
            status_stmt = select(ServerStatus).where(
                ServerStatus.uuid.in_(uuids)
            )
            statuses = (await session.execute(status_stmt)).scalars().all()
            status_map = {s.uuid: s for s in statuses}

            # 批量查每台服务器的最新 load（使用子查询）
            # 取每个 server_uuid 最新一条 load_now
            from sqlalchemy import func

            latest_time_sub = (
                select(
                    LoadNow.server_uuid,
                    func.max(LoadNow.time).label("max_time"),
                )
                .where(LoadNow.server_uuid.in_(uuids))
                .group_by(LoadNow.server_uuid)
                .subquery()
            )
            load_stmt = (
                select(LoadNow)
                .join(
                    latest_time_sub,
                    (LoadNow.server_uuid == latest_time_sub.c.server_uuid)
                    & (LoadNow.time == latest_time_sub.c.max_time),
                )
            )
            loads = (await session.execute(load_stmt)).scalars().all()
            load_map = {ld.server_uuid: ld for ld in loads}

        # 只广播 status==1 的服务器
        servers_data: list[dict[str, Any]] = []
        for server in servers:
            ss = status_map.get(server.uuid)
            if not ss or ss.status != 1:
                continue
            ld = load_map.get(server.uuid)

            entry: dict[str, Any] = {
                "uuid": server.uuid,
                "name": server.name,
                "top": server.top,
                # 硬件概览
                "cpu_name": server.cpu_name,
                "cpu_cores": server.cpu_cores,
                "arch": server.arch,
                "os": server.os,
                "region": server.region,
                "mem_total": server.mem_total,
                "swap_total": server.swap_total,
                "disk_total": server.disk_total,
                "virtualization": server.virtualization,
                # 状态
                "status": ss.status,
                "last_online": ss.last_online,
            }

            if ld:
                entry["load"] = {
                    "cpu": ld.cpu,
                    "ram": ld.ram,
                    "ram_total": ld.ram_total,
                    "swap": ld.swap,
                    "swap_total": ld.swap_total,
                    "load": ld.load,
                    "disk": ld.disk,
                    "disk_total": ld.disk_total,
                    "net_in": ld.net_in,
                    "net_out": ld.net_out,
                    "tcp": ld.tcp,
                    "udp": ld.udp,
                    "process": ld.process,
                }
            else:
                entry["load"] = None

            servers_data.append(entry)

        return {
            "type": "snapshot",
            "timestamp": now,
            "servers": servers_data,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Task 3: 监控数据清理
    # ─────────────────────────────────────────────────────────────────────────

    async def _purge_old_load(self) -> None:
        """定期清除过期的 load_now 记录.

        保留 LOAD_RETAIN_SECONDS 内的数据，
        清理周期 = 保留时长（至少每 60 秒一次）。
        """
        interval = max(settings.LOAD_RETAIN_SECONDS, 60)

        while True:
            try:
                cutoff = int(time.time()) - settings.LOAD_RETAIN_SECONDS

                async with async_session_factory() as session:
                    result = await session.execute(
                        delete(LoadNow).where(LoadNow.time < cutoff)
                    )
                    deleted = result.rowcount or 0
                    await session.commit()

                    if deleted:
                        print(f"🧹 已清理 {deleted} 条过期监控记录")

            except Exception as e:
                print(f"⚠️ 数据清理任务出错: {e}")

            await asyncio.sleep(interval)


# 全局任务管理器实例
background_tasks = BackgroundTasks()

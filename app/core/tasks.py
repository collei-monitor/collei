"""后台任务模块.

管理所有定时任务和后台作业:
  1. 离线检测 — 基于内存缓存检测超时服务器并标记为离线
  2. 广播快照 — 从内存缓存构建快照推送给 WebSocket 客户端
  3. 数据清理 — 定期清除过期的 load_now 监控记录
  4. 网络探测数据清理
  5. load_now → load_minute 降采样
  6. load_minute → load_hour 降采样
  7. load_minute 数据清理
  8. load_hour 数据清理
  9. 计费管理 — 自动续期过期服务器、定期重算周期流量
  10. 审计日志清理
"""

from __future__ import annotations

import asyncio
import calendar
import time
from datetime import datetime, timezone

from sqlalchemy import update, delete

from app.core.audit import audit
from app.core.config import settings
from app.core.config_cache import config_cache
from app.core.server_cache import server_cache
from app.db.session import async_session_factory
from app.models.clients import ServerStatus, ServerBillingRule
from app.models.monitoring import LoadNow, LoadMinute, LoadHour
from app.models.network import NetworkStatus


class BackgroundTasks:
    """后台任务管理器."""

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """启动所有后台任务."""
        self._tasks.append(asyncio.create_task(self._check_offline_servers()))
        self._tasks.append(asyncio.create_task(self._broadcast_snapshot()))
        self._tasks.append(asyncio.create_task(self._purge_old_load()))
        self._tasks.append(asyncio.create_task(self._purge_old_network_status()))
        self._tasks.append(asyncio.create_task(self._billing_check()))
        self._tasks.append(asyncio.create_task(self._downsample_to_minute()))
        self._tasks.append(asyncio.create_task(self._downsample_to_hour()))
        self._tasks.append(asyncio.create_task(self._purge_old_load_minute()))
        self._tasks.append(asyncio.create_task(self._purge_old_load_hour()))
        self._tasks.append(asyncio.create_task(self._purge_old_logs()))
        self._tasks.append(asyncio.create_task(self._check_updates()))

        # 启动告警状态机引擎
        from app.core.alert_engine import alert_engine
        await alert_engine.start()

        # 启动 DDNS 引擎
        from app.core.ddns_engine import ddns_engine
        await ddns_engine.start()

        await audit.background(
            msg_type="system", message="后台任务已启动", source="tasks")

    async def stop(self) -> None:
        """停止所有后台任务."""
        # 停止告警引擎
        from app.core.alert_engine import alert_engine
        await alert_engine.stop()

        # 停止 DDNS 引擎
        from app.core.ddns_engine import ddns_engine
        await ddns_engine.stop()

        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        await audit.background(
            msg_type="system", message="后台任务已停止", source="tasks")

    # ─────────────────────────────────────────────────────────────────────────
    # Task 1: 离线检测
    # ─────────────────────────────────────────────────────────────────────────

    async def _check_offline_servers(self) -> None:
        """基于内存缓存检测并标记离线服务器.

        规则: 缓存中 status == 1 (在线)
              且 last_online < now - offline_threshold_seconds
              → 更新缓存并写回数据库 status = 0 (离线)

        启动时会等待一个宽限期（= offline_threshold_seconds），
        避免重启后因 last_online 是旧值而将所有服务器误判为离线。
        """
        # 启动宽限期: 等待足够时间让 Agent 恢复心跳，避免重启后误判全部离线
        grace = int(config_cache.get("offline_threshold_seconds") or 10)
        await asyncio.sleep(grace)

        while True:
            interval = 2  # fallback
            try:
                offline_threshold = int(config_cache.get("offline_threshold_seconds") or 10)
                interval = int(config_cache.get("offline_check_interval") or 2)
                threshold = int(time.time()) - offline_threshold

                offline_uuids = server_cache.get_online_before(threshold)

                if offline_uuids:
                    # 先更新缓存（同时清除 run_id 和冲突信息）
                    server_cache.mark_offline(offline_uuids)
                    # 再写回数据库（清除 status 和 current_run_id）
                    async with async_session_factory() as session:
                        await session.execute(
                            update(ServerStatus)
                            .where(ServerStatus.uuid.in_(offline_uuids))
                            .values(status=0, current_run_id=None)
                        )
                        await session.commit()
                    await audit.background(
                        level="warning",
                        msg_type="server",
                        message=f"检测到 {len(offline_uuids)} 台服务器离线",
                        detail=", ".join(offline_uuids),
                        source="offline_check",
                    )

            except Exception as e:
                await audit.error(
                    msg_type="error",
                    message="离线检测任务出错",
                    exc=e,
                    source="offline_check",
                )

            await asyncio.sleep(interval)

    # ─────────────────────────────────────────────────────────────────────────
    # Task 2: WebSocket 广播
    # ─────────────────────────────────────────────────────────────────────────

    async def _broadcast_snapshot(self) -> None:
        """从内存缓存构建数据并广播给所有 WebSocket 客户端.

        - 当节点数据变更时（_nodes_dirty），广播 type="nodes"
        - 每个周期固定广播 type="status"
        """
        from app.core.ws_manager import ws_manager

        while True:
            try:
                if ws_manager.has_connections:
                    # 节点变更时推送全量节点列表
                    if server_cache.nodes_dirty:
                        server_cache.clear_nodes_dirty()
                        public_nodes = server_cache.build_nodes(include_hidden=False)
                        full_nodes = server_cache.build_nodes(include_hidden=True)
                        await ws_manager.broadcast(public_nodes, full_nodes)

                    # 定时推送状态快照
                    public_status = server_cache.build_status(include_hidden=False, include_io=False)
                    full_status = server_cache.build_status(include_hidden=True, include_io=True)
                    await ws_manager.broadcast(public_status, full_status)

            except Exception as e:
                await audit.error(
                    msg_type="error",
                    message="广播快照出错",
                    exc=e,
                    source="broadcast",
                )

            await asyncio.sleep(settings.WS_BROADCAST_INTERVAL)

    # ─────────────────────────────────────────────────────────────────────────
    # Task 3: 监控数据清理
    # ─────────────────────────────────────────────────────────────────────────

    async def _purge_old_load(self) -> None:
        """定期清除过期的 load_now 记录.

        保留 load_retain_seconds 内的数据，
        清理周期 = load_retain_seconds * 2。
        """
        while True:
            interval = 160  # fallback
            try:
                load_retain = int(config_cache.get("load_retain_seconds") or 80)
                interval = load_retain * 2
                cutoff = int(time.time()) - load_retain

                async with async_session_factory() as session:
                    result = await session.execute(
                        delete(LoadNow).where(LoadNow.time < cutoff)
                    )
                    await session.commit()

            except Exception as e:
                await audit.error(
                    msg_type="error",
                    message="load_now 清理任务出错",
                    exc=e,
                    source="purge_load_now",
                )

            await asyncio.sleep(interval)

    # ─────────────────────────────────────────────────────────────────────────
    # Task 4: 网络探测数据清理
    # ─────────────────────────────────────────────────────────────────────────

    async def _purge_old_network_status(self) -> None:
        """定期清除过期的 network_status 探测记录.

        保留 network_status_retain_hours 小时内的数据，
        清理周期 = retain_hours / 2（至少 1 小时）。
        """
        while True:
            interval = 3600  # fallback 1h
            try:
                retain_hours = int(
                    config_cache.get("network_status_retain_hours") or 24,
                )
                interval = max(retain_hours * 1800, 3600)  # retain/2, 最少 1h
                cutoff = int(time.time()) - retain_hours * 3600

                async with async_session_factory() as session:
                    result = await session.execute(
                        delete(NetworkStatus).where(NetworkStatus.time < cutoff)
                    )
                    await session.commit()

            except Exception as e:
                await audit.error(
                    msg_type="error",
                    message="网络探测数据清理任务出错",
                    exc=e,
                    source="purge_network",
                )

            await asyncio.sleep(interval)

    # ─────────────────────────────────────────────────────────────────────────
    # Task 5: load_now → load_minute 降采样 (每 60 秒)
    # ─────────────────────────────────────────────────────────────────────────

    async def _downsample_to_minute(self) -> None:
        """定期将 load_now 数据降采样到 load_minute.

        每 60 秒执行一次，对上一分钟的 load_now 数据取平均写入 load_minute。
        时间窗口: 向下取整到分钟边界。
        """
        from app.crud import monitoring as crud_monitoring

        await asyncio.sleep(60)  # 启动延迟，等待首批数据
        while True:
            try:
                now = int(time.time())
                # 上一分钟的窗口
                window_end = now - (now % 60)
                window_start = window_end - 60

                server_uuids = list(server_cache._statuses.keys())

                # 每台服务器单独会话提交，避免单个长事务长时间占用连接
                for uuid in server_uuids:
                    async with async_session_factory() as session:
                        await crud_monitoring.downsample_to_minute(
                            session, uuid,
                            window_start=window_start,
                            window_end=window_end,
                        )
                        await session.commit()

            except Exception as e:
                await audit.error(
                    msg_type="error",
                    message="load_now→load_minute 降采样出错",
                    exc=e,
                    source="downsample_minute",
                )

            await asyncio.sleep(60)

    # ─────────────────────────────────────────────────────────────────────────
    # Task 6: load_minute → load_hour 降采样 (每 600 秒)
    # ─────────────────────────────────────────────────────────────────────────

    async def _downsample_to_hour(self) -> None:
        """定期将 load_minute 数据降采样到 load_hour.

        每 600 秒 (10 分钟) 执行一次，对上一个 10 分钟窗口的 load_minute 数据聚合写入 load_hour。
        """
        from app.crud import monitoring as crud_monitoring

        await asyncio.sleep(120)  # 启动延迟
        while True:
            try:
                now = int(time.time())
                # 上一个 10 分钟窗口
                window_end = now - (now % 600)
                window_start = window_end - 600

                server_uuids = list(server_cache._statuses.keys())

                # 每台服务器单独会话提交，避免单个长事务长时间占用连接
                for uuid in server_uuids:
                    async with async_session_factory() as session:
                        await crud_monitoring.downsample_to_hour(
                            session, uuid,
                            window_start=window_start,
                            window_end=window_end,
                        )
                        await session.commit()

            except Exception as e:
                await audit.error(
                    msg_type="error",
                    message="load_minute→load_hour 降采样出错",
                    exc=e,
                    source="downsample_hour",
                )

            await asyncio.sleep(600)

    # ─────────────────────────────────────────────────────────────────────────
    # Task 7: load_minute 数据清理
    # ─────────────────────────────────────────────────────────────────────────

    async def _purge_old_load_minute(self) -> None:
        """定期清除过期的 load_minute 记录.

        保留 load_minute_retain_hours 小时内的数据，默认 24h。
        清理周期 = retain_hours / 2（至少 10 分钟）。
        """
        from app.crud import monitoring as crud_monitoring

        while True:
            interval = 43200  # fallback 12h
            try:
                retain_hours = int(
                    config_cache.get("load_minute_retain_hours") or 24
                )
                interval = max(retain_hours * 1800, 600)
                cutoff = int(time.time()) - retain_hours * 3600

                async with async_session_factory() as session:
                    await crud_monitoring.purge_old_load_minute(
                        session, before=cutoff
                    )
                    await session.commit()

            except Exception as e:
                await audit.error(
                    msg_type="error",
                    message="load_minute 清理任务出错",
                    exc=e,
                    source="purge_load_minute",
                )

            await asyncio.sleep(interval)

    # ─────────────────────────────────────────────────────────────────────────
    # Task 8: load_hour 数据清理
    # ─────────────────────────────────────────────────────────────────────────

    async def _purge_old_load_hour(self) -> None:
        """定期清除过期的 load_hour 记录.

        保留 load_hour_retain_hours 小时内的数据，默认 72h。
        清理周期 = retain_hours / 2（至少 1 小时）。
        """
        from app.crud import monitoring as crud_monitoring

        while True:
            interval = 129600  # fallback 36h
            try:
                retain_hours = int(
                    config_cache.get("load_hour_retain_hours") or 72
                )
                interval = max(retain_hours * 1800, 3600)
                cutoff = int(time.time()) - retain_hours * 3600

                async with async_session_factory() as session:
                    await crud_monitoring.purge_old_load_hour(
                        session, before=cutoff
                    )
                    await session.commit()

            except Exception as e:
                await audit.error(
                    msg_type="error",
                    message="load_hour 清理任务出错",
                    exc=e,
                    source="purge_load_hour",
                )

            await asyncio.sleep(interval)

    # ─────────────────────────────────────────────────────────────────────────
    # Task 9: 计费管理（自动续期 + 周期流量重算）
    # ─────────────────────────────────────────────────────────────────────────

    async def _billing_check(self) -> None:
        """定期检查计费状态.

        1. 自动续期：在线且已过期的服务器自动延长一个计费周期
        2. 周期流量重算：从数据库重新计算周期流量（修正增量累加偏差）

        每 60 秒执行一次。
        """
        while True:
            try:
                now = int(time.time())
                # 遍历缓存中的计费规则
                for uuid, rule in list(server_cache._billing_rules.items()):
                    expiry = rule.get("expiry_date")
                    cycle = rule.get("billing_cycle")
                    if not expiry or not cycle:
                        continue

                    # 服务器在线且已过期 → 自动续期
                    st = server_cache._statuses.get(uuid)
                    if st and st.get("status") == 1 and expiry < now:
                        new_expiry = self._add_months(expiry, cycle)
                        # 更新数据库
                        async with async_session_factory() as session:
                            await session.execute(
                                update(ServerBillingRule)
                                .where(ServerBillingRule.uuid == uuid)
                                .values(expiry_date=new_expiry)
                            )
                            await session.commit()
                        # 更新缓存
                        rule["expiry_date"] = new_expiry
                        await audit.background(
                            msg_type="billing",
                            message=f"服务器已自动续期至 {new_expiry}",
                            source="billing_check",
                            server_uuid=uuid,
                        )

                # 从数据库重新计算周期流量
                async with async_session_factory() as session:
                    await server_cache.recalc_cycle_traffic(session)

            except Exception as e:
                await audit.error(
                    msg_type="error",
                    message="计费检查任务出错",
                    exc=e,
                    source="billing_check",
                )

            await asyncio.sleep(60)

    # ─────────────────────────────────────────────────────────────────────────
    # Task 10: 审计日志清理
    # ─────────────────────────────────────────────────────────────────────────

    async def _purge_old_logs(self) -> None:
        """定期清除过期的审计日志记录.

        保留 log_retain_days 天内的数据，默认 30 天。
        清理周期 = retain_days / 2（至少 6 小时）。
        """
        from app.crud import notification as crud_notification

        while True:
            interval = 43200  # fallback 12h
            try:
                retain_days = int(
                    config_cache.get("log_retain_days") or 30
                )
                interval = max(retain_days * 43200, 21600)  # retain/2, 最少 6h
                cutoff = int(time.time()) - retain_days * 86400

                async with async_session_factory() as session:
                    deleted = await crud_notification.purge_old_logs(
                        session, before=cutoff
                    )
                    await session.commit()
                    if deleted:
                        await audit.background(
                            msg_type="task",
                            message=f"已清理 {deleted} 条过期审计日志",
                            source="purge_logs",
                        )

            except Exception as e:
                await audit.error(
                    msg_type="error",
                    message="审计日志清理任务出错",
                    exc=e,
                    source="purge_logs",
                )

            await asyncio.sleep(interval)

    # ─────────────────────────────────────────────────────────────────────────
    # Task 11: 版本更新检查
    # ─────────────────────────────────────────────────────────────────────────

    async def _check_updates(self) -> None:
        """每小时检查一次 GitHub Releases，比较版本并缓存 changelog."""
        from app.core.update_checker import update_checker

        while True:
            try:
                await update_checker.check()
            except Exception as e:
                await audit.error(
                    msg_type="error",
                    message="版本检查任务出错",
                    exc=e,
                    source="check_updates",
                )
            await asyncio.sleep(3600)

    @staticmethod
    def _add_months(timestamp: int, months: int) -> int:
        """将时间戳增加指定月数."""
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        month = dt.month - 1 + months
        year = dt.year + month // 12
        month = month % 12 + 1
        day = min(dt.day, calendar.monthrange(year, month)[1])
        result = datetime(
            year, month, day, dt.hour, dt.minute, dt.second, tzinfo=timezone.utc,
        )
        return int(result.timestamp())


# 全局任务管理器实例
background_tasks = BackgroundTasks()

"""后台任务模块.

管理所有定时任务和后台作业:
  1. 离线检测 — 基于内存缓存检测超时服务器并标记为离线（启动首次从数据库检测）
  2. 广播快照 — 从内存缓存构建快照推送给 WebSocket 客户端
  3. 数据清理 — 定期清除过期的 load_now 监控记录（周期 = load_retain_seconds * 2）
"""

from __future__ import annotations

import asyncio
import time

from sqlalchemy import update, delete

from app.core.config import settings
from app.core.config_cache import config_cache
from app.core.server_cache import server_cache
from app.db.session import async_session_factory
from app.models.clients import ServerStatus
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

        # 启动告警状态机引擎
        from app.core.alert_engine import alert_engine
        await alert_engine.start()

        print("✅ 后台任务已启动")

    async def stop(self) -> None:
        """停止所有后台任务."""
        # 停止告警引擎
        from app.core.alert_engine import alert_engine
        await alert_engine.stop()

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
        """基于内存缓存检测并标记离线服务器.

        规则: 缓存中 status == 1 (在线)
              且 last_online < now - offline_threshold_seconds
              → 更新缓存并写回数据库 status = 0 (离线)
        """
        while True:
            interval = 2  # fallback
            try:
                offline_threshold = int(config_cache.get("offline_threshold_seconds") or 10)
                interval = int(config_cache.get("offline_check_interval") or 2)
                threshold = int(time.time()) - offline_threshold

                offline_uuids = server_cache.get_online_before(threshold)

                if offline_uuids:
                    # 先更新缓存
                    server_cache.mark_offline(offline_uuids)
                    # 再写回数据库
                    async with async_session_factory() as session:
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
                    public_status = server_cache.build_status(include_hidden=False)
                    full_status = server_cache.build_status(include_hidden=True)
                    await ws_manager.broadcast(public_status, full_status)

            except Exception as e:
                print(f"⚠️ 广播快照出错: {e}")

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
                    deleted = result.rowcount or 0
                    await session.commit()

                    if deleted:
                        print(f"🧹 已清理 {deleted} 条过期监控记录")

            except Exception as e:
                print(f"⚠️ 数据清理任务出错: {e}")

            await asyncio.sleep(interval)


# 全局任务管理器实例
background_tasks = BackgroundTasks()

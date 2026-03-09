"""服务器数据内存缓存 — 启动时从数据库预加载，探针上报时同步更新.

替代后台任务中频繁的数据库查询，离线检测和 WebSocket 广播快照均从内存读取。

用法:
  # 启动时预加载
  await server_cache.preload(session)

  # 探针上报后更新
  server_cache.update_server(uuid, info_dict)
  server_cache.update_status(uuid, status=1, last_online=now)
  server_cache.update_load(uuid, load_dict)

  # 后台任务使用
  offline = server_cache.get_online_before(threshold_ts)
  snapshot = server_cache.build_snapshot()
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clients import Server, ServerStatus
from app.models.monitoring import LoadNow

# 广播快照需要的 Server 字段
_SERVER_FIELDS = (
    "uuid", "name", "top", "cpu_name", "cpu_cores", "arch", "os",
    "region", "mem_total", "swap_total", "disk_total", "virtualization",
    "hidden", "is_approved", "created_at",
)

# LoadNow 中需要缓存的字段
_LOAD_FIELDS = (
    "cpu", "ram", "ram_total", "swap", "swap_total", "load",
    "disk", "disk_total", "net_in", "net_out", "tcp", "udp", "process",
)


class ServerCache:
    """服务器数据内存缓存（asyncio 单事件循环下线程安全）."""

    def __init__(self) -> None:
        # uuid → 服务器静态信息
        self._servers: dict[str, dict[str, Any]] = {}
        # uuid → {status, last_online, boot_time}
        self._statuses: dict[str, dict[str, Any]] = {}
        # uuid → 最新 load 数据
        self._loads: dict[str, dict[str, Any]] = {}

    # ─────────────────────────────────────────────────────────────────────
    # 预加载
    # ─────────────────────────────────────────────────────────────────────

    async def preload(self, db: AsyncSession) -> None:
        """从数据库全量加载已批准服务器的信息、状态、最新负载到缓存."""
        # 加载所有已批准的服务器
        stmt = select(Server).where(Server.is_approved == 1)
        servers = (await db.execute(stmt)).scalars().all()

        self._servers.clear()
        for s in servers:
            self._servers[s.uuid] = {
                f: getattr(s, f) for f in _SERVER_FIELDS
            }

        uuids = list(self._servers.keys())
        if not uuids:
            self._statuses.clear()
            self._loads.clear()
            return

        # 加载状态
        status_stmt = select(ServerStatus).where(ServerStatus.uuid.in_(uuids))
        statuses = (await db.execute(status_stmt)).scalars().all()
        self._statuses = {
            ss.uuid: {
                "status": ss.status,
                "last_online": ss.last_online,
                "boot_time": ss.boot_time,
            }
            for ss in statuses
        }

        # 加载每台服务器的最新 load
        latest_sub = (
            select(
                LoadNow.server_uuid,
                func.max(LoadNow.time).label("max_time"),
            )
            .where(LoadNow.server_uuid.in_(uuids))
            .group_by(LoadNow.server_uuid)
            .subquery()
        )
        load_stmt = select(LoadNow).join(
            latest_sub,
            (LoadNow.server_uuid == latest_sub.c.server_uuid)
            & (LoadNow.time == latest_sub.c.max_time),
        )
        loads = (await db.execute(load_stmt)).scalars().all()
        self._loads = {
            ld.server_uuid: {f: getattr(ld, f) for f in _LOAD_FIELDS}
            for ld in loads
        }

    # ─────────────────────────────────────────────────────────────────────
    # 写入 / 更新
    # ─────────────────────────────────────────────────────────────────────

    def update_server(self, uuid: str, info: dict[str, Any]) -> None:
        """更新或新增服务器静态信息（仅缓存已有字段）."""
        existing = self._servers.get(uuid)
        if existing is None:
            # 新服务器：仅在 is_approved==1 时入缓存
            if info.get("is_approved") != 1:
                return
            self._servers[uuid] = {f: info.get(f) for f in _SERVER_FIELDS}
            self._servers[uuid]["uuid"] = uuid
        else:
            for k, v in info.items():
                if k in existing:
                    existing[k] = v

    def update_status(
        self,
        uuid: str,
        *,
        status: int | None = None,
        last_online: int | None = None,
        boot_time: int | None = None,
    ) -> None:
        """更新服务器状态缓存."""
        existing = self._statuses.get(uuid)
        if existing is None:
            self._statuses[uuid] = {
                "status": status or 0,
                "last_online": last_online,
                "boot_time": boot_time,
            }
        else:
            if status is not None:
                existing["status"] = status
            if last_online is not None:
                existing["last_online"] = last_online
            if boot_time is not None:
                existing["boot_time"] = boot_time

    def update_load(self, uuid: str, load_dict: dict[str, Any]) -> None:
        """更新服务器最新负载缓存."""
        self._loads[uuid] = {
            f: load_dict.get(f) for f in _LOAD_FIELDS
        }

    def remove_server(self, uuid: str) -> None:
        """从缓存中移除服务器及其关联数据."""
        self._servers.pop(uuid, None)
        self._statuses.pop(uuid, None)
        self._loads.pop(uuid, None)

    # ─────────────────────────────────────────────────────────────────────
    # 离线检测
    # ─────────────────────────────────────────────────────────────────────

    def get_online_before(self, threshold_ts: int) -> list[str]:
        """返回当前在线但 last_online < threshold_ts 的服务器 UUID 列表."""
        result: list[str] = []
        for uuid, st in self._statuses.items():
            if st["status"] == 1 and (st["last_online"] or 0) < threshold_ts:
                result.append(uuid)
        return result

    def mark_offline(self, uuids: list[str]) -> None:
        """在缓存中将指定服务器标记为离线."""
        for uuid in uuids:
            st = self._statuses.get(uuid)
            if st:
                st["status"] = 0

    # ─────────────────────────────────────────────────────────────────────
    # 广播快照
    # ─────────────────────────────────────────────────────────────────────

    def build_snapshot(self) -> dict[str, Any]:
        """从缓存构建完整的服务器状态快照（仅在线 + 已批准 + 未隐藏）."""
        now = int(time.time())
        servers_data: list[dict[str, Any]] = []

        # 按 top desc, created_at desc 排序
        sorted_servers = sorted(
            self._servers.values(),
            key=lambda s: (s.get("top") or 0, s.get("created_at") or 0),
            reverse=True,
        )

        for srv in sorted_servers:
            uuid = srv["uuid"]
            # 仅已批准且未隐藏
            if srv.get("is_approved") != 1 or srv.get("hidden", 0) != 0:
                continue

            st = self._statuses.get(uuid)
            if not st or st["status"] != 1:
                continue

            entry: dict[str, Any] = {
                "uuid": uuid,
                "name": srv.get("name"),
                "top": srv.get("top"),
                "cpu_name": srv.get("cpu_name"),
                "cpu_cores": srv.get("cpu_cores"),
                "arch": srv.get("arch"),
                "os": srv.get("os"),
                "region": srv.get("region"),
                "mem_total": srv.get("mem_total"),
                "swap_total": srv.get("swap_total"),
                "disk_total": srv.get("disk_total"),
                "virtualization": srv.get("virtualization"),
                "status": st["status"],
                "last_online": st["last_online"],
            }

            ld = self._loads.get(uuid)
            entry["load"] = dict(ld) if ld else None

            servers_data.append(entry)

        return {
            "type": "snapshot",
            "timestamp": now,
            "servers": servers_data,
        }


# 全局单例
server_cache = ServerCache()

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
  snapshot = server_cache.build_status()
  nodes = server_cache.build_nodes()
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clients import Group, Server, ServerBillingRule, ServerGroup, ServerStatus
from app.models.monitoring import LoadNow

# 广播快照需要的 Server 字段（含 token 用于缓存鉴权）
_SERVER_FIELDS = (
    "uuid", "name", "top", "cpu_name", "cpu_cores", "arch", "os",
    "region", "mem_total", "swap_total", "disk_total", "virtualization",
    "hidden", "is_approved", "created_at", "token", "enable_statistics_mode",
)

# LoadNow 中需要缓存的字段
_LOAD_FIELDS = (
    "cpu", "ram", "ram_total", "swap", "swap_total", "load",
    "disk", "disk_total", "net_in", "net_out", "tcp", "udp", "process",
)

# Group 需要缓存的字段
_GROUP_FIELDS = ("id", "name", "top", "created_at")

# BillingRule 需要缓存的字段
_BILLING_FIELDS = (
    "uuid", "billing_cycle", "billing_cycle_data", "billing_cycle_cost",
    "traffic_reset_day", "traffic_threshold", "accounting_mode",
    "billing_cycle_cost_code", "expiry_date",
)


class ServerCache:
    """服务器数据内存缓存（asyncio 单事件循环下线程安全）."""

    def __init__(self) -> None:
        # uuid → 服务器静态信息
        self._servers: dict[str, dict[str, Any]] = {}
        # uuid → {status, last_online, boot_time, total_flow_out, total_flow_in}
        self._statuses: dict[str, dict[str, Any]] = {}
        # uuid → 最新 load 数据
        self._loads: dict[str, dict[str, Any]] = {}
        # token → uuid 反向索引
        self._token_index: dict[str, str] = {}
        # group_id → 分组信息
        self._groups: dict[str, dict[str, Any]] = {}
        # server_uuid → [group_id, ...]
        self._server_groups: dict[str, list[str]] = {}
        # group_id → [server_uuid, ...]
        self._group_servers: dict[str, list[str]] = {}
        # uuid → 计费规则
        self._billing_rules: dict[str, dict[str, Any]] = {}
        # uuid → 当前周期已用流量
        self._cycle_traffic: dict[str, int] = {}
        # 节点数据是否变更（服务器/分组增删改时置 True）
        self._nodes_dirty: bool = False

    # ─────────────────────────────────────────────────────────────────────
    # 预加载
    # ─────────────────────────────────────────────────────────────────────

    async def preload(self, db: AsyncSession) -> None:
        """从数据库全量加载已批准服务器的信息、状态、最新负载、分组及计费规则到缓存."""
        # 加载所有已批准的服务器
        stmt = select(Server).where(Server.is_approved == 1)
        servers = (await db.execute(stmt)).scalars().all()

        self._servers.clear()
        self._token_index.clear()
        for s in servers:
            self._servers[s.uuid] = {
                f: getattr(s, f) for f in _SERVER_FIELDS
            }
            if s.token:
                self._token_index[s.token] = s.uuid

        uuids = list(self._servers.keys())
        if not uuids:
            self._statuses.clear()
            self._loads.clear()
            self._groups.clear()
            self._server_groups.clear()
            self._group_servers.clear()
            self._billing_rules.clear()
            self._cycle_traffic.clear()
            return

        # 加载状态
        status_stmt = select(ServerStatus).where(ServerStatus.uuid.in_(uuids))
        statuses = (await db.execute(status_stmt)).scalars().all()
        self._statuses = {
            ss.uuid: {
                "status": ss.status,
                "last_online": ss.last_online,
                "boot_time": ss.boot_time,
                "total_flow_out": ss.total_flow_out,
                "total_flow_in": ss.total_flow_in,
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

        # 加载分组
        all_groups = (await db.execute(select(Group))).scalars().all()
        self._groups = {
            g.id: {f: getattr(g, f) for f in _GROUP_FIELDS}
            for g in all_groups
        }

        # 加载服务器-分组关联
        all_sg = (await db.execute(select(ServerGroup))).scalars().all()
        self._server_groups.clear()
        self._group_servers.clear()
        for sg in all_sg:
            self._server_groups.setdefault(sg.server_uuid, []).append(sg.group_id)
            self._group_servers.setdefault(sg.group_id, []).append(sg.server_uuid)

        # 加载计费规则
        all_rules = (await db.execute(select(ServerBillingRule))).scalars().all()
        self._billing_rules = {
            r.uuid: {f: getattr(r, f) for f in _BILLING_FIELDS}
            for r in all_rules
        }

        # 批量计算周期流量
        await self._recalc_cycle_traffic(db)

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
            if info.get("token"):
                self._token_index[info["token"]] = uuid
        else:
            old_token = existing.get("token")
            for k, v in info.items():
                if k in existing:
                    existing[k] = v
            new_token = existing.get("token")
            # 维护 token 反向索引
            if old_token and old_token != new_token:
                self._token_index.pop(old_token, None)
            if new_token:
                self._token_index[new_token] = uuid
        self._nodes_dirty = True

    def update_status(
        self,
        uuid: str,
        *,
        status: int | None = None,
        last_online: int | None = None,
        boot_time: int | None = None,
        total_flow_out: int | None = None,
        total_flow_in: int | None = None,
    ) -> None:
        """更新服务器状态缓存."""
        existing = self._statuses.get(uuid)
        if existing is None:
            self._statuses[uuid] = {
                "status": status or 0,
                "last_online": last_online,
                "boot_time": boot_time,
                "total_flow_out": total_flow_out,
                "total_flow_in": total_flow_in,
            }
        else:
            if status is not None:
                existing["status"] = status
            if last_online is not None:
                existing["last_online"] = last_online
            if boot_time is not None:
                existing["boot_time"] = boot_time
            if total_flow_out is not None:
                existing["total_flow_out"] = total_flow_out
            if total_flow_in is not None:
                existing["total_flow_in"] = total_flow_in

    def update_load(self, uuid: str, load_dict: dict[str, Any]) -> None:
        """更新服务器最新负载缓存."""
        self._loads[uuid] = {
            f: load_dict.get(f) for f in _LOAD_FIELDS
        }

    def remove_server(self, uuid: str) -> None:
        """从缓存中移除服务器及其关联数据."""
        srv = self._servers.pop(uuid, None)
        if srv and srv.get("token"):
            self._token_index.pop(srv["token"], None)
        self._statuses.pop(uuid, None)
        self._loads.pop(uuid, None)
        # 清理分组关联
        group_ids = self._server_groups.pop(uuid, [])
        for gid in group_ids:
            uuids = self._group_servers.get(gid)
            if uuids and uuid in uuids:
                uuids.remove(uuid)
        self._nodes_dirty = True

    def get_uuid_by_token(self, token: str) -> str | None:
        """通过 token 查找 uuid（O(1)，仅覆盖 is_approved==1 的服务器）."""
        return self._token_index.get(token)

    # ─────────────────────────────────────────────────────────────────────
    # 分组缓存管理
    # ─────────────────────────────────────────────────────────────────────

    def update_group(self, group_id: str, info: dict[str, Any]) -> None:
        """新增或更新分组缓存."""
        existing = self._groups.get(group_id)
        if existing is None:
            self._groups[group_id] = {f: info.get(f) for f in _GROUP_FIELDS}
            self._groups[group_id]["id"] = group_id
        else:
            for k, v in info.items():
                if k in existing:
                    existing[k] = v
        self._nodes_dirty = True

    def remove_group(self, group_id: str) -> None:
        """从缓存中移除分组及其关联."""
        self._groups.pop(group_id, None)
        server_uuids = self._group_servers.pop(group_id, [])
        for uuid in server_uuids:
            gids = self._server_groups.get(uuid)
            if gids and group_id in gids:
                gids.remove(group_id)
        self._nodes_dirty = True

    def set_group_servers(self, group_id: str, server_uuids: list[str]) -> None:
        """全量替换分组关联的服务器."""
        old_uuids = self._group_servers.get(group_id, [])
        for uuid in old_uuids:
            gids = self._server_groups.get(uuid)
            if gids and group_id in gids:
                gids.remove(group_id)
        self._group_servers[group_id] = list(server_uuids)
        for uuid in server_uuids:
            self._server_groups.setdefault(uuid, [])
            if group_id not in self._server_groups[uuid]:
                self._server_groups[uuid].append(group_id)
        self._nodes_dirty = True

    def set_server_groups(self, server_uuid: str, group_ids: list[str]) -> None:
        """全量替换服务器所属分组."""
        old_gids = self._server_groups.get(server_uuid, [])
        for gid in old_gids:
            uuids = self._group_servers.get(gid)
            if uuids and server_uuid in uuids:
                uuids.remove(server_uuid)
        self._server_groups[server_uuid] = list(group_ids)
        for gid in group_ids:
            self._group_servers.setdefault(gid, [])
            if server_uuid not in self._group_servers[gid]:
                self._group_servers[gid].append(server_uuid)
        self._nodes_dirty = True

    # ─────────────────────────────────────────────────────────────────────
    # 计费规则缓存管理
    # ─────────────────────────────────────────────────────────────────────

    def update_billing_rule(self, uuid: str, info: dict[str, Any]) -> None:
        """新增或更新计费规则缓存."""
        existing = self._billing_rules.get(uuid)
        if existing is None:
            self._billing_rules[uuid] = {f: info.get(f) for f in _BILLING_FIELDS}
            self._billing_rules[uuid]["uuid"] = uuid
        else:
            for k, v in info.items():
                if k in existing:
                    existing[k] = v
        self._nodes_dirty = True

    def remove_billing_rule(self, uuid: str) -> None:
        """移除计费规则缓存."""
        self._billing_rules.pop(uuid, None)
        self._cycle_traffic.pop(uuid, None)
        self._nodes_dirty = True

    def get_billing_rule(self, uuid: str) -> dict[str, Any] | None:
        """获取缓存中的计费规则."""
        return self._billing_rules.get(uuid)

    def update_cycle_traffic(self, uuid: str, traffic_used: int) -> None:
        """设置服务器当前周期已用流量."""
        self._cycle_traffic[uuid] = traffic_used

    def add_cycle_traffic(self, uuid: str, net_in: int, net_out: int) -> None:
        """增量累加周期流量（Agent 上报时调用）."""
        rule = self._billing_rules.get(uuid)
        if not rule:
            return
        from app.crud.monitoring import calc_traffic_used
        increment = calc_traffic_used(net_in, net_out, rule.get("accounting_mode"))
        self._cycle_traffic[uuid] = self._cycle_traffic.get(uuid, 0) + increment

    async def _recalc_cycle_traffic(self, db: AsyncSession) -> None:
        """从数据库重新计算所有有计费规则的服务器的周期流量."""
        from app.crud.monitoring import batch_get_cycle_traffic
        if not self._billing_rules:
            self._cycle_traffic.clear()
            return
        rules_list = list(self._billing_rules.values())
        self._cycle_traffic = await batch_get_cycle_traffic(db, rules_list)

    async def recalc_cycle_traffic(self, db: AsyncSession) -> None:
        """公开方法：重新计算周期流量."""
        await self._recalc_cycle_traffic(db)

    def build_billing_brief(self, uuid: str) -> dict[str, Any] | None:
        """构建计费摘要信息."""
        rule = self._billing_rules.get(uuid)
        if not rule:
            return None
        return {
            "billing_cycle": rule.get("billing_cycle"),
            "billing_cycle_cost": rule.get("billing_cycle_cost"),
            "billing_cycle_cost_code": rule.get("billing_cycle_cost_code"),
            "traffic_threshold": rule.get("traffic_threshold"),
            "traffic_used": self._cycle_traffic.get(uuid, 0),
            "accounting_mode": rule.get("accounting_mode"),
            "expiry_date": rule.get("expiry_date"),
        }

    # ─────────────────────────────────────────────────────────────────────
    # 节点变更标记
    # ─────────────────────────────────────────────────────────────────────

    @property
    def nodes_dirty(self) -> bool:
        return self._nodes_dirty

    def clear_nodes_dirty(self) -> None:
        self._nodes_dirty = False

    def mark_nodes_dirty(self) -> None:
        self._nodes_dirty = True

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
    # 广播数据构建
    # ─────────────────────────────────────────────────────────────────────

    def build_nodes(self, *, include_hidden: bool = False) -> dict[str, Any]:
        """构建节点列表与分组信息（type="nodes"）.

        包含所有已批准服务器（含离线），附带分组信息。
        """
        now = int(time.time())
        servers_data: list[dict[str, Any]] = []

        sorted_servers = sorted(
            self._servers.values(),
            key=lambda s: (s.get("top") or 0, s.get("created_at") or 0),
            reverse=True,
        )

        for srv in sorted_servers:
            uuid = srv["uuid"]
            if srv.get("is_approved") != 1:
                continue
            if not include_hidden and srv.get("hidden", 0) != 0:
                continue

            st = self._statuses.get(uuid, {})
            group_ids = self._server_groups.get(uuid, [])
            groups = [
                dict(self._groups[gid])
                for gid in group_ids if gid in self._groups
            ]

            servers_data.append({
                "uuid": uuid,
                "name": srv.get("name"),
                "cpu_name": srv.get("cpu_name"),
                "arch": srv.get("arch"),
                "os": srv.get("os"),
                "region": srv.get("region"),
                "top": srv.get("top"),
                "status": st.get("status", 0),
                "last_online": st.get("last_online"),
                "boot_time": st.get("boot_time"),
                "groups": groups,
                "billing": self.build_billing_brief(uuid),
            })

        # 构建分组列表（含 server_uuids）
        visible_uuids = {s["uuid"] for s in servers_data}
        sorted_groups = sorted(
            self._groups.values(),
            key=lambda g: (g.get("top") or 0, g.get("created_at") or 0),
            reverse=True,
        )
        groups_data: list[dict[str, Any]] = []
        for grp in sorted_groups:
            gid = grp["id"]
            all_uuids = self._group_servers.get(gid, [])
            groups_data.append({
                **grp,
                "server_uuids": [u for u in all_uuids if u in visible_uuids],
            })

        return {
            "type": "nodes",
            "timestamp": now,
            "servers": servers_data,
            "groups": groups_data,
        }

    def build_status(self, *, include_hidden: bool = False) -> dict[str, Any]:
        """构建快照状态数据（type="status"）.

        以 uuid 为键的字典形式返回，包含静态信息 + 状态 + 负载。
        """
        now = int(time.time())
        servers_data: dict[str, dict[str, Any]] = {}

        for srv in self._servers.values():
            uuid = srv["uuid"]
            if srv.get("is_approved") != 1:
                continue
            if not include_hidden and srv.get("hidden", 0) != 0:
                continue

            st = self._statuses.get(uuid)
            if not st or st["status"] != 1:
                continue

            ld = self._loads.get(uuid)

            servers_data[uuid] = {
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
                "enable_statistics_mode": srv.get("enable_statistics_mode"),
                "status": {
                    "status": st["status"],
                    "last_online": st.get("last_online"),
                    "boot_time": st.get("boot_time"),
                    "total_flow_out": st.get("total_flow_out"),
                    "total_flow_in": st.get("total_flow_in"),
                },
                "load": dict(ld) if ld else None,
            }

        return {
            "type": "status",
            "timestamp": now,
            "servers": servers_data,
        }


# 全局单例
server_cache = ServerCache()

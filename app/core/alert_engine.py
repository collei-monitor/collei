"""告警状态机引擎.

完全在内存中维护 OK → PENDING → FIRING 三态状态机，
asyncio 后台任务每隔 5 秒轮询评估所有启用的告警规则。

状态转换:
  OK      → PENDING  (指标超标)
  PENDING → FIRING   (持续超标且达到 duration)
  PENDING → OK       (指标恢复)
  FIRING  → OK       (指标恢复，发送恢复通知)

文档参考: collei_sql.md § 4.1 alert_rules — 架构设计
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Any

from sqlalchemy import select, update as sa_update

from app.core.server_cache import server_cache
from app.db.session import async_session_factory
from app.models.clients import ServerGroup
from app.models.notification import (
    AlertChannel,
    AlertHistory,
    AlertRule,
    AlertRuleMapping,
    MessageSenderProvider,
)

logger = logging.getLogger(__name__)


# ─── 状态枚举 ─────────────────────────────────────────────────────────────────

class AlertStatus(str, Enum):
    OK = "ok"
    PENDING = "pending"
    FIRING = "firing"


# ─── 单条状态记录 ─────────────────────────────────────────────────────────────

class _AlertState:
    """单个 (server_uuid, rule_id) 对的内存状态."""
    __slots__ = ("status", "pending_since", "last_notified_at", "value")

    def __init__(self) -> None:
        self.status: AlertStatus = AlertStatus.OK
        self.pending_since: float = 0.0
        self.last_notified_at: float = 0.0
        self.value: float = 0.0


# ─── 条件比较 ─────────────────────────────────────────────────────────────────

_COMPARATORS: dict[str, Any] = {
    ">": float.__gt__,
    "<": float.__lt__,
    ">=": float.__ge__,
    "<=": float.__le__,
    "==": float.__eq__,
    "!=": float.__ne__,
}


def _compare(value: float, condition: str, threshold: float) -> bool:
    fn = _COMPARATORS.get(condition)
    if fn is None:
        return False
    return fn(value, threshold)


# ─── 引擎主体 ─────────────────────────────────────────────────────────────────

class AlertEngine:
    """告警状态机引擎 — 全局单例."""

    def __init__(self) -> None:
        # (server_uuid, rule_id) → _AlertState
        self._states: dict[tuple[str, int], _AlertState] = {}

        # 缓存的规则/映射/分组
        self._rules: dict[int, dict[str, Any]] = {}
        self._mappings: dict[int, list[dict[str, Any]]] = {}
        self._group_servers: dict[str, set[str]] = {}

        self._task: asyncio.Task | None = None

    # ── 生命周期 ────────────────────────────────────────────────────────

    async def start(self) -> None:
        """预加载配置并启动后台轮询任务."""
        await self.reload()
        self._task = asyncio.create_task(self._loop())
        logger.info("✅ 告警引擎已启动")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("ℹ️ 告警引擎已停止")

    # ── 配置热重载 ──────────────────────────────────────────────────────

    async def reload(self) -> None:
        """从数据库重新加载启用的规则、映射、渠道、分组关系."""
        async with async_session_factory() as db:
            # 1) 启用的规则
            rows = (await db.execute(
                select(AlertRule).where(AlertRule.enabled == 1)
            )).scalars().all()
            self._rules = {
                r.id: {
                    "id": r.id,
                    "name": r.name,
                    "metric": r.metric,
                    "condition": r.condition,
                    "threshold": r.threshold,
                    "duration": r.duration,
                }
                for r in rows
            }

            # 2) 映射
            self._mappings.clear()
            if self._rules:
                maps = (await db.execute(
                    select(AlertRuleMapping).where(
                        AlertRuleMapping.rule_id.in_(self._rules.keys())
                    )
                )).scalars().all()
                for m in maps:
                    self._mappings.setdefault(m.rule_id, []).append({
                        "target_type": m.target_type,
                        "target_id": m.target_id,
                        "channel_id": m.channel_id,
                    })

            # 3) 分组 → 服务器
            self._group_servers.clear()
            links = (await db.execute(select(ServerGroup))).scalars().all()
            for gl in links:
                self._group_servers.setdefault(
                    gl.group_id, set()
                ).add(gl.server_uuid)

        # 清理已失效的状态条目
        valid_keys: set[tuple[str, int]] = set()
        for rule_id in self._rules:
            for uuid in self._resolve_servers(rule_id):
                valid_keys.add((uuid, rule_id))
        stale = set(self._states) - valid_keys
        for k in stale:
            del self._states[k]

        logger.info(
            "告警引擎重载: %d 条规则, %d 条映射",
            len(self._rules),
            sum(len(v) for v in self._mappings.values()),
        )

    # ── 目标解析 ────────────────────────────────────────────────────────

    def _resolve_servers(self, rule_id: int) -> set[str]:
        """将规则映射的 target_type/target_id 解析为具体的 server_uuid 集合."""
        mappings = self._mappings.get(rule_id, [])
        servers: set[str] = set()
        all_uuids = set(server_cache._servers)

        for m in mappings:
            tt, tid = m["target_type"], m["target_id"]
            if tt == "global":
                servers.update(all_uuids)
            elif tt == "server":
                if tid in all_uuids:
                    servers.add(tid)
            elif tt == "group":
                servers.update(
                    self._group_servers.get(tid, set()) & all_uuids
                )
        return servers

    def _channel_ids_for_rule(self, rule_id: int) -> set[int]:
        """获取某规则关联的所有渠道 ID."""
        return {m["channel_id"] for m in self._mappings.get(rule_id, [])}

    # ── 指标评估 ────────────────────────────────────────────────────────

    def _evaluate(
        self, server_uuid: str, rule: dict[str, Any],
    ) -> tuple[bool, float]:
        """评估服务器是否触发规则条件，返回 (是否触发, 当前值)."""
        metric = rule["metric"]
        condition = rule["condition"]
        threshold = rule["threshold"]

        status_data = server_cache._statuses.get(server_uuid, {})
        load_data = server_cache._loads.get(server_uuid, {})

        # 离线指标：status==0 视为 value=1
        if metric == "offline":
            value = 1.0 if status_data.get("status", 0) == 0 else 0.0
            return _compare(value, condition, threshold), value

        # 其余指标需要服务器在线
        if status_data.get("status", 0) != 1:
            return False, 0.0

        value: float
        if metric == "cpu":
            value = (load_data.get("cpu") or 0) / 100.0
        elif metric == "ram":
            used = load_data.get("ram") or 0
            total = load_data.get("ram_total") or 0
            value = used / total if total > 0 else 0.0
        elif metric == "swap":
            used = load_data.get("swap") or 0
            total = load_data.get("swap_total") or 0
            value = used / total if total > 0 else 0.0
        elif metric == "disk":
            used = load_data.get("disk") or 0
            total = load_data.get("disk_total") or 0
            value = used / total if total > 0 else 0.0
        elif metric == "load":
            value = float(load_data.get("load") or 0)
        elif metric in ("net_in", "traffic_in"):
            value = float(load_data.get("net_in") or 0)
        elif metric in ("net_out", "traffic_out"):
            value = float(load_data.get("net_out") or 0)
        elif metric == "tcp":
            value = float(load_data.get("tcp") or 0)
        elif metric == "udp":
            value = float(load_data.get("udp") or 0)
        elif metric == "process":
            value = float(load_data.get("process") or 0)
        else:
            return False, 0.0

        return _compare(value, condition, threshold), value

    # ── 后台轮询 ────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        """每 5 秒执行一次全量评估."""
        while True:
            try:
                await self._tick()
            except Exception:
                logger.exception("告警引擎评估出错")
            await asyncio.sleep(5)

    async def _tick(self) -> None:
        """单次评估：遍历所有启用规则 × 映射服务器，推进状态机."""
        now = time.time()

        for rule_id, rule in self._rules.items():
            server_uuids = self._resolve_servers(rule_id)
            channel_ids = self._channel_ids_for_rule(rule_id)

            for uuid in server_uuids:
                key = (uuid, rule_id)
                state = self._states.get(key)
                if state is None:
                    state = _AlertState()
                    self._states[key] = state

                triggered, value = self._evaluate(uuid, rule)
                state.value = value

                # ── 状态转换 ────────────────────────────────────
                if state.status == AlertStatus.OK:
                    if triggered:
                        state.status = AlertStatus.PENDING
                        state.pending_since = now

                elif state.status == AlertStatus.PENDING:
                    if not triggered:
                        state.status = AlertStatus.OK
                        state.pending_since = 0.0
                    elif now - state.pending_since >= rule["duration"]:
                        state.status = AlertStatus.FIRING
                        state.last_notified_at = now
                        await self._on_firing(uuid, rule, value, channel_ids)

                elif state.status == AlertStatus.FIRING:
                    if not triggered:
                        state.status = AlertStatus.OK
                        state.pending_since = 0.0
                        await self._on_resolved(uuid, rule, value, channel_ids)

    # ── 事件回调 ────────────────────────────────────────────────────────

    async def _on_firing(
        self,
        server_uuid: str,
        rule: dict[str, Any],
        value: float,
        channel_ids: set[int],
    ) -> None:
        """FIRING 触发：写入 alert_history 并发送告警通知."""
        # 写历史
        async with async_session_factory() as db:
            db.add(AlertHistory(
                server_uuid=server_uuid,
                rule_id=rule["id"],
                status="firing",
                value=value,
            ))
            await db.commit()

        # 构造消息
        srv = server_cache._servers.get(server_uuid, {})
        server_name = srv.get("name", server_uuid)
        message = (
            f"🔴 告警触发: {rule['name']}\n"
            f"服务器: {server_name} ({server_uuid})\n"
            f"指标: {rule['metric']} {rule['condition']} {rule['threshold']}\n"
            f"当前值: {value:.4f}"
        )

        await self._notify(channel_ids, message)
        logger.warning(
            "🔴 告警: %s | %s | %s=%.4f",
            rule["name"], server_name, rule["metric"], value,
        )

    async def _on_resolved(
        self,
        server_uuid: str,
        rule: dict[str, Any],
        value: float,
        channel_ids: set[int],
    ) -> None:
        """FIRING → OK：更新 alert_history 并发送恢复通知."""
        now_ts = int(time.time())
        async with async_session_factory() as db:
            await db.execute(
                sa_update(AlertHistory)
                .where(
                    AlertHistory.server_uuid == server_uuid,
                    AlertHistory.rule_id == rule["id"],
                    AlertHistory.status == "firing",
                )
                .values(status="resolved", updated_at=now_ts)
            )
            await db.commit()

        srv = server_cache._servers.get(server_uuid, {})
        server_name = srv.get("name", server_uuid)
        message = (
            f"🟢 告警恢复: {rule['name']}\n"
            f"服务器: {server_name} ({server_uuid})\n"
            f"指标: {rule['metric']} 已恢复正常\n"
            f"当前值: {value:.4f}"
        )

        await self._notify(channel_ids, message)
        logger.info(
            "🟢 恢复: %s | %s | %s=%.4f",
            rule["name"], server_name, rule["metric"], value,
        )

    async def _notify(self, channel_ids: set[int], message: str) -> None:
        """向指定渠道集合发送通知（实时查库获取最新配置）."""
        from app.core.notifier import send_notification

        async with async_session_factory() as db:
            for cid in channel_ids:
                row = (await db.execute(
                    select(AlertChannel).where(AlertChannel.id == cid)
                )).scalar_one_or_none()
                if not row:
                    continue
                prov = (await db.execute(
                    select(MessageSenderProvider).where(
                        MessageSenderProvider.id == row.provider_id
                    )
                )).scalar_one_or_none()
                ch = {
                    "name": row.name,
                    "target": row.target,
                    "provider_type": prov.type if prov else None,
                    "addition": prov.addition if prov else None,
                }
                await send_notification(ch, message)

    # ── 前端查询接口 ──────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """返回引擎整体状态概览."""
        firing = sum(
            1 for s in self._states.values()
            if s.status == AlertStatus.FIRING
        )
        pending = sum(
            1 for s in self._states.values()
            if s.status == AlertStatus.PENDING
        )
        return {
            "running": self._task is not None and not self._task.done(),
            "rules_count": len(self._rules),
            "mappings_count": sum(len(v) for v in self._mappings.values()),
            "states_count": len(self._states),
            "firing_count": firing,
            "pending_count": pending,
        }

    def get_all_states(self) -> list[dict[str, Any]]:
        """返回所有状态机条目的快照（用于前端展示）."""
        result: list[dict[str, Any]] = []
        for (uuid, rule_id), state in self._states.items():
            rule = self._rules.get(rule_id, {})
            srv = server_cache._servers.get(uuid, {})
            result.append({
                "server_uuid": uuid,
                "server_name": srv.get("name"),
                "rule_id": rule_id,
                "rule_name": rule.get("name"),
                "metric": rule.get("metric"),
                "status": state.status.value,
                "value": state.value,
                "pending_since": state.pending_since,
                "last_notified_at": state.last_notified_at,
            })
        return result


# 全局单例
alert_engine = AlertEngine()

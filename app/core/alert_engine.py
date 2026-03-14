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
    AlertRuleChannelLink,
    AlertRuleTarget,
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
    __slots__ = (
        "status", "pending_since", "last_notified_at", "value",
        "last_notified_level",
    )

    def __init__(self) -> None:
        self.status: AlertStatus = AlertStatus.OK
        self.pending_since: float = 0.0
        self.last_notified_at: float = 0.0
        self.value: float = 0.0
        # traffic_percent 梯度通知：上次已通知的百分比档位
        self.last_notified_level: float = 0.0


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

        # 缓存的规则/目标/渠道/分组
        self._rules: dict[int, dict[str, Any]] = {}
        self._targets: dict[int, list[dict[str, Any]]] = {}
        self._channels: dict[int, set[int]] = {}
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
                    "notify_recovery": r.notify_recovery,
                    "custom_message": r.custom_message,
                    "traffic_notify_step": r.traffic_notify_step,
                }
                for r in rows
            }

            # 2) 目标绑定
            self._targets.clear()
            self._channels.clear()
            if self._rules:
                rule_ids = list(self._rules.keys())
                targets = (await db.execute(
                    select(AlertRuleTarget).where(
                        AlertRuleTarget.rule_id.in_(rule_ids)
                    )
                )).scalars().all()
                for t in targets:
                    self._targets.setdefault(t.rule_id, []).append({
                        "target_type": t.target_type,
                        "target_id": t.target_id,
                        "is_exclude": t.is_exclude,
                    })

                # 3) 渠道绑定
                ch_links = (await db.execute(
                    select(AlertRuleChannelLink).where(
                        AlertRuleChannelLink.rule_id.in_(rule_ids)
                    )
                )).scalars().all()
                for cl in ch_links:
                    self._channels.setdefault(cl.rule_id, set()).add(
                        cl.channel_id)

            # 4) 分组 → 服务器
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
            "告警引擎重载: %d 条规则, %d 条目标绑定, %d 条渠道绑定",
            len(self._rules),
            sum(len(v) for v in self._targets.values()),
            sum(len(v) for v in self._channels.values()),
        )

    # ── 目标解析 ────────────────────────────────────────────────────────

    def _resolve_servers(self, rule_id: int) -> set[str]:
        """将规则目标绑定解析为具体的 server_uuid 集合（支持排除模式）."""
        target_list = self._targets.get(rule_id, [])
        servers: set[str] = set()
        excludes: set[str] = set()
        all_uuids = set(server_cache._servers)

        for t in target_list:
            tt, tid = t["target_type"], t["target_id"]
            is_exclude = t.get("is_exclude", 0)
            resolved: set[str] = set()

            if tt == "global":
                resolved = set(all_uuids)
            elif tt == "server":
                if tid in all_uuids:
                    resolved = {tid}
            elif tt == "group":
                resolved = self._group_servers.get(tid, set()) & all_uuids

            if is_exclude:
                excludes.update(resolved)
            else:
                servers.update(resolved)

        return servers - excludes

    def _channel_ids_for_rule(self, rule_id: int) -> set[int]:
        """获取某规则关联的所有渠道 ID."""
        return set(self._channels.get(rule_id, set()))

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

        # 到期提醒 — value=距到期天数, threshold=提前多少天提醒
        if metric == "expiry":
            billing = server_cache._billing_rules.get(server_uuid)
            if not billing or not billing.get("expiry_date"):
                return False, 0.0
            days_left = (billing["expiry_date"] - time.time()) / 86400.0
            value = days_left
            return _compare(value, condition, threshold), value

        # 周期流量百分比 — value=当前使用百分比
        if metric == "traffic_percent":
            billing = server_cache._billing_rules.get(server_uuid)
            if not billing:
                return False, 0.0
            traffic_threshold = billing.get("traffic_threshold") or 0
            if traffic_threshold <= 0:
                return False, 0.0
            traffic_used = server_cache._cycle_traffic.get(server_uuid, 0)
            pct = (traffic_used / traffic_threshold) * 100.0
            value = pct
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
            metric = rule["metric"]

            # login 是事件驱动指标，不参与轮询评估
            if metric == "login":
                continue

            server_uuids = self._resolve_servers(rule_id)
            channel_ids = self._channel_ids_for_rule(rule_id)

            for uuid in server_uuids:
                key = (uuid, rule_id)
                state = self._states.get(key)
                is_new = state is None
                if state is None:
                    state = _AlertState()
                    self._states[key] = state

                triggered, value = self._evaluate(uuid, rule)
                state.value = value

                # ── traffic_percent 梯度通知特殊逻辑 ────────────
                if metric == "traffic_percent":
                    step = rule.get("traffic_notify_step") or 0
                    if triggered and step > 0:
                        # 计算当前所处档位 (threshold, threshold+step, ...)
                        base = rule["threshold"]
                        current_level = base + (
                            int((value - base) / step) * step
                        )
                        if current_level > state.last_notified_level:
                            state.last_notified_level = current_level
                            state.status = AlertStatus.FIRING
                            state.last_notified_at = now
                            if not is_new:
                                await self._on_firing(
                                    uuid, rule, value, channel_ids)
                    elif triggered and not is_new:
                        # 无 step 但超阈值：普通单次告警
                        if state.status == AlertStatus.OK:
                            state.status = AlertStatus.FIRING
                            state.last_notified_at = now
                            await self._on_firing(
                                uuid, rule, value, channel_ids)
                    elif not triggered:
                        if state.status != AlertStatus.OK:
                            old_status = state.status
                            state.status = AlertStatus.OK
                            state.last_notified_level = 0.0
                            if old_status == AlertStatus.FIRING \
                                    and rule.get("notify_recovery", 0) == 1:
                                await self._on_resolved(
                                    uuid, rule, value, channel_ids)
                    elif is_new and triggered:
                        state.status = AlertStatus.FIRING
                        state.last_notified_at = now
                        state.last_notified_level = rule["threshold"]
                    continue

                # ── 通用状态机逻辑 ──────────────────────────────

                # 新建状态条目且条件已满足时，直接置为
                # FIRING 但不发送通知，避免对已有状态
                # （如服务器早已离线）误触发告警风暴
                if is_new and triggered:
                    state.status = AlertStatus.FIRING
                    state.last_notified_at = now
                    continue

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
                        if rule.get("notify_recovery", 0) == 1:
                            await self._on_resolved(
                                uuid, rule, value, channel_ids)

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

        custom = rule.get("custom_message")
        if custom:
            message = self._render_template(
                custom, server_name=server_name, server_uuid=server_uuid,
                rule=rule, value=value, event="firing",
            )
        else:
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

        custom = rule.get("custom_message")
        if custom:
            message = self._render_template(
                custom, server_name=server_name, server_uuid=server_uuid,
                rule=rule, value=value, event="resolved",
            )
        else:
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

    # ── 模板渲染 ──────────────────────────────────────────────────────

    @staticmethod
    def _render_template(
        template: str,
        *,
        server_name: str,
        server_uuid: str,
        rule: dict[str, Any],
        value: float,
        event: str,
    ) -> str:
        """渲染自定义消息模板，替换变量占位符."""
        return template.format_map({
            "server_name": server_name,
            "server_uuid": server_uuid,
            "metric": rule.get("metric", ""),
            "value": f"{value:.4f}",
            "threshold": rule.get("threshold", ""),
            "rule_name": rule.get("name", ""),
            "condition": rule.get("condition", ""),
            "event": event,
        })

    # ── 事件驱动通知 ──────────────────────────────────────────────────

    async def notify_login(
        self,
        *,
        username: str,
        ip: str | None = None,
        user_agent: str | None = None,
        login_method: str = "password",
    ) -> None:
        """登录成功时由 auth 层调用，向 metric='login' 的规则渠道发送通知."""
        for rule_id, rule in self._rules.items():
            if rule["metric"] != "login":
                continue
            channel_ids = self._channel_ids_for_rule(rule_id)
            if not channel_ids:
                continue

            custom = rule.get("custom_message")
            if custom:
                message = custom.format_map({
                    "username": username,
                    "ip": ip or "unknown",
                    "user_agent": user_agent or "unknown",
                    "login_method": login_method,
                    "rule_name": rule.get("name", ""),
                })
            else:
                message = (
                    f"🔑 新登录通知\n"
                    f"用户: {username}\n"
                    f"IP: {ip or 'unknown'}\n"
                    f"方式: {login_method}\n"
                    f"UA: {user_agent or 'unknown'}"
                )

            await self._notify(channel_ids, message)
            logger.info("🔑 登录通知: %s from %s", username, ip)

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
            "mappings_count": sum(len(v) for v in self._targets.values()),
            "channels_count": sum(len(v) for v in self._channels.values()),
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

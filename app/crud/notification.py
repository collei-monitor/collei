"""告警与通知的 CRUD / DAO 操作."""

from __future__ import annotations

from typing import Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import (
    AlertChannel,
    AlertHistory,
    AlertRule,
    AlertRuleChannelLink,
    AlertRuleTarget,
    MessageSenderProvider,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Message Sender Provider
# ═══════════════════════════════════════════════════════════════════════════════

async def get_provider(db: AsyncSession, provider_id: int) -> MessageSenderProvider | None:
    result = await db.execute(
        select(MessageSenderProvider).where(MessageSenderProvider.id == provider_id))
    return result.scalar_one_or_none()


async def get_all_providers(db: AsyncSession) -> Sequence[MessageSenderProvider]:
    result = await db.execute(select(MessageSenderProvider))
    return result.scalars().all()


async def create_provider(
    db: AsyncSession, *, name: str | None = None,
    type: str | None = None, addition: str | None = None,
) -> MessageSenderProvider:
    provider = MessageSenderProvider(name=name, type=type, addition=addition)
    db.add(provider)
    await db.flush()
    return provider


async def update_provider(
    db: AsyncSession, provider_id: int, **kwargs,
) -> MessageSenderProvider | None:
    await db.execute(
        update(MessageSenderProvider)
        .where(MessageSenderProvider.id == provider_id)
        .values(**kwargs)
    )
    await db.flush()
    return await get_provider(db, provider_id)


async def delete_provider(db: AsyncSession, provider_id: int) -> bool:
    result = await db.execute(
        delete(MessageSenderProvider).where(MessageSenderProvider.id == provider_id))
    return (result.rowcount or 0) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Alert Channel
# ═══════════════════════════════════════════════════════════════════════════════

async def get_channel(db: AsyncSession, channel_id: int) -> AlertChannel | None:
    result = await db.execute(
        select(AlertChannel).where(AlertChannel.id == channel_id))
    return result.scalar_one_or_none()


async def get_all_channels(db: AsyncSession) -> Sequence[AlertChannel]:
    result = await db.execute(select(AlertChannel))
    return result.scalars().all()


async def create_channel(
    db: AsyncSession,
    *,
    name: str,
    provider_id: int,
    target: str | None = None,
) -> AlertChannel:
    channel = AlertChannel(
        name=name, provider_id=provider_id, target=target)
    db.add(channel)
    await db.flush()
    return channel


async def update_channel(
    db: AsyncSession, channel_id: int, **kwargs,
) -> AlertChannel | None:
    await db.execute(
        update(AlertChannel)
        .where(AlertChannel.id == channel_id)
        .values(**kwargs)
    )
    await db.flush()
    return await get_channel(db, channel_id)


async def delete_channel(db: AsyncSession, channel_id: int) -> bool:
    result = await db.execute(
        delete(AlertChannel).where(AlertChannel.id == channel_id))
    return (result.rowcount or 0) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Alert Rule
# ═══════════════════════════════════════════════════════════════════════════════

async def get_rule(db: AsyncSession, rule_id: int) -> AlertRule | None:
    result = await db.execute(
        select(AlertRule).where(AlertRule.id == rule_id))
    return result.scalar_one_or_none()


async def get_all_rules(db: AsyncSession) -> Sequence[AlertRule]:
    result = await db.execute(
        select(AlertRule).order_by(AlertRule.created_at.desc()))
    return result.scalars().all()


async def create_rule(db: AsyncSession, **kwargs) -> AlertRule:
    rule = AlertRule(**kwargs)
    db.add(rule)
    await db.flush()
    return rule


async def update_rule(
    db: AsyncSession, rule_id: int, **kwargs,
) -> AlertRule | None:
    await db.execute(
        update(AlertRule).where(AlertRule.id == rule_id).values(**kwargs))
    await db.flush()
    return await get_rule(db, rule_id)


async def delete_rule(db: AsyncSession, rule_id: int) -> bool:
    result = await db.execute(
        delete(AlertRule).where(AlertRule.id == rule_id))
    return (result.rowcount or 0) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Alert Rule Targets (规则目标绑定)
# ═══════════════════════════════════════════════════════════════════════════════

async def get_rule_targets(
    db: AsyncSession, rule_id: int,
) -> Sequence[AlertRuleTarget]:
    result = await db.execute(
        select(AlertRuleTarget).where(AlertRuleTarget.rule_id == rule_id))
    return result.scalars().all()


async def add_rule_targets(
    db: AsyncSession,
    *,
    rule_id: int,
    targets: list[dict],
) -> tuple[list[AlertRuleTarget], list[dict]]:
    """增量添加规则的目标绑定，跳过已存在的条目，返回 (created, skipped)."""
    existing = await db.execute(
        select(AlertRuleTarget).where(AlertRuleTarget.rule_id == rule_id))
    existing_keys = {
        (t.target_type, t.target_id) for t in existing.scalars().all()
    }

    created: list[AlertRuleTarget] = []
    skipped: list[dict] = []
    for t in targets:
        key = (t["target_type"], t["target_id"])
        if key in existing_keys:
            skipped.append(t)
            continue
        obj = AlertRuleTarget(
            rule_id=rule_id,
            target_type=t["target_type"],
            target_id=t["target_id"],
            is_exclude=t.get("is_exclude", 0),
        )
        db.add(obj)
        created.append(obj)
        existing_keys.add(key)

    if created:
        await db.flush()
    return created, skipped


async def delete_rule_targets_batch(
    db: AsyncSession,
    *,
    rule_id: int,
    items: list[dict],
) -> int:
    """批量删除规则的指定目标绑定."""
    count = 0
    for item in items:
        result = await db.execute(
            delete(AlertRuleTarget).where(
                AlertRuleTarget.rule_id == rule_id,
                AlertRuleTarget.target_type == item["target_type"],
                AlertRuleTarget.target_id == item["target_id"],
            )
        )
        count += result.rowcount or 0
    return count


async def delete_all_rule_targets(db: AsyncSession, rule_id: int) -> int:
    result = await db.execute(
        delete(AlertRuleTarget).where(AlertRuleTarget.rule_id == rule_id))
    return result.rowcount or 0


# ═══════════════════════════════════════════════════════════════════════════════
# Alert Rule Channels (规则渠道绑定)
# ═══════════════════════════════════════════════════════════════════════════════

async def get_rule_channels(
    db: AsyncSession, rule_id: int,
) -> Sequence[AlertRuleChannelLink]:
    result = await db.execute(
        select(AlertRuleChannelLink).where(
            AlertRuleChannelLink.rule_id == rule_id))
    return result.scalars().all()


async def set_rule_channels(
    db: AsyncSession,
    *,
    rule_id: int,
    channel_ids: list[int],
) -> list[AlertRuleChannelLink]:
    """完全替换规则绑定的通知渠道（先删后增）."""
    await db.execute(
        delete(AlertRuleChannelLink).where(
            AlertRuleChannelLink.rule_id == rule_id))
    objs = [
        AlertRuleChannelLink(rule_id=rule_id, channel_id=cid)
        for cid in channel_ids
    ]
    if objs:
        db.add_all(objs)
        await db.flush()
    return objs


# ═══════════════════════════════════════════════════════════════════════════════
# Alert History
# ═══════════════════════════════════════════════════════════════════════════════

async def get_alert_history(
    db: AsyncSession,
    *,
    server_uuid: str | None = None,
    rule_id: int | None = None,
    limit: int = 50,
) -> Sequence[AlertHistory]:
    """获取告警历史，支持按服务器/规则过滤."""
    stmt = select(AlertHistory)
    if server_uuid:
        stmt = stmt.where(AlertHistory.server_uuid == server_uuid)
    if rule_id:
        stmt = stmt.where(AlertHistory.rule_id == rule_id)
    stmt = stmt.order_by(AlertHistory.created_at.desc()).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_active_alert(
    db: AsyncSession,
    *,
    server_uuid: str,
    rule_id: int,
) -> AlertHistory | None:
    """获取某服务器某规则当前活跃的告警（status=firing）."""
    result = await db.execute(
        select(AlertHistory).where(
            AlertHistory.server_uuid == server_uuid,
            AlertHistory.rule_id == rule_id,
            AlertHistory.status == "firing",
        )
    )
    return result.scalar_one_or_none()


async def create_alert_history(
    db: AsyncSession, **kwargs,
) -> AlertHistory:
    record = AlertHistory(**kwargs)
    db.add(record)
    await db.flush()
    return record


async def update_alert_history(
    db: AsyncSession, history_id: int, **kwargs,
) -> AlertHistory | None:
    await db.execute(
        update(AlertHistory)
        .where(AlertHistory.id == history_id)
        .values(**kwargs)
    )
    await db.flush()
    result = await db.execute(
        select(AlertHistory).where(AlertHistory.id == history_id))
    return result.scalar_one_or_none()

"""告警与通知的 CRUD / DAO 操作."""

from __future__ import annotations

from typing import Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import (
    AlertChannel,
    AlertHistory,
    AlertRule,
    AlertRuleMapping,
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
# Alert Rule Mapping
# ═══════════════════════════════════════════════════════════════════════════════

async def get_rule_mappings(
    db: AsyncSession, rule_id: int,
) -> Sequence[AlertRuleMapping]:
    result = await db.execute(
        select(AlertRuleMapping).where(AlertRuleMapping.rule_id == rule_id))
    return result.scalars().all()


async def create_rule_mapping(
    db: AsyncSession,
    *,
    rule_id: int,
    target_type: str,
    target_id: str,
    channel_id: int,
) -> AlertRuleMapping:
    mapping = AlertRuleMapping(
        rule_id=rule_id,
        target_type=target_type,
        target_id=target_id,
        channel_id=channel_id,
    )
    db.add(mapping)
    await db.flush()
    return mapping


async def delete_rule_mapping(
    db: AsyncSession,
    *,
    rule_id: int,
    target_type: str,
    target_id: str,
    channel_id: int,
) -> bool:
    result = await db.execute(
        delete(AlertRuleMapping).where(
            AlertRuleMapping.rule_id == rule_id,
            AlertRuleMapping.target_type == target_type,
            AlertRuleMapping.target_id == target_id,
            AlertRuleMapping.channel_id == channel_id,
        )
    )
    return (result.rowcount or 0) > 0


async def delete_all_rule_mappings(db: AsyncSession, rule_id: int) -> int:
    """删除某规则的所有映射."""
    result = await db.execute(
        delete(AlertRuleMapping).where(AlertRuleMapping.rule_id == rule_id))
    return result.rowcount or 0


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

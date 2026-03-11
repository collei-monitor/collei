"""告警与通知相关的 SQLAlchemy 模型.

对应数据库文档 § 4 — Notifications & Tasks:
  alert_rules, alert_channels, alert_rule_mapping, alert_history
对应数据库文档 § 5 — Configs & Misc:
  message_sender_providers, logs
"""

import time

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    Float,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base


# ─── helpers ──────────────────────────────────────────────────────────────────

def _now() -> int:
    return int(time.time())


# ─── Alert Rules ──────────────────────────────────────────────────────────────

class AlertRule(Base):
    """告警规则表 — 定义触发告警的条件."""

    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    metric: Mapped[str] = mapped_column(String, nullable=False)
    condition: Mapped[str] = mapped_column(String, nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    duration: Mapped[int] = mapped_column(
        Integer, default=60, server_default=text("60"))
    enabled: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    created_at: Mapped[int] = mapped_column(Integer, default=_now)

    # 关系
    mappings: Mapped[list["AlertRuleMapping"]] = relationship(
        "AlertRuleMapping", back_populates="rule",
        cascade="all, delete-orphan",
    )
    history: Mapped[list["AlertHistory"]] = relationship(
        "AlertHistory", back_populates="rule",
        cascade="all, delete-orphan",
    )


# ─── Message Sender Providers ─────────────────────────────────────────────────

class MessageSenderProvider(Base):
    """消息发送提供商配置 — Telegram Bot, Webhook, 邮件等."""

    __tablename__ = "message_sender_providers"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String)
    type: Mapped[str | None] = mapped_column(String)
    addition: Mapped[str | None] = mapped_column(Text)

    channels: Mapped[list["AlertChannel"]] = relationship(
        "AlertChannel", back_populates="provider",
        cascade="all, delete-orphan",
    )


# ─── Alert Channels ──────────────────────────────────────────────────────────

class AlertChannel(Base):
    """通知渠道/策略表 — 告警触发后的通知目标."""

    __tablename__ = "alert_channels"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    provider_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("message_sender_providers.id", ondelete="CASCADE"),
    )
    target: Mapped[str | None] = mapped_column(String)

    provider: Mapped["MessageSenderProvider"] = relationship(
        "MessageSenderProvider", back_populates="channels")
    mappings: Mapped[list["AlertRuleMapping"]] = relationship(
        "AlertRuleMapping", back_populates="channel",
        cascade="all, delete-orphan",
    )


# ─── Alert Rule Mapping ──────────────────────────────────────────────────────

class AlertRuleMapping(Base):
    """告警规则与服务器/分组的映射关系."""

    __tablename__ = "alert_rule_mapping"

    rule_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("alert_rules.id", ondelete="CASCADE"),
        primary_key=True,
    )
    target_type: Mapped[str] = mapped_column(String, primary_key=True)
    target_id: Mapped[str] = mapped_column(String, primary_key=True)
    channel_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("alert_channels.id", ondelete="CASCADE"),
    )

    rule: Mapped["AlertRule"] = relationship(
        "AlertRule", back_populates="mappings")
    channel: Mapped["AlertChannel"] = relationship(
        "AlertChannel", back_populates="mappings")


# ─── Alert History ────────────────────────────────────────────────────────────

class AlertHistory(Base):
    """告警触发与静默记录 — 防止 Alert Storm."""

    __tablename__ = "alert_history"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True)
    server_uuid: Mapped[str] = mapped_column(
        String,
        ForeignKey("servers.uuid", ondelete="CASCADE"),
    )
    rule_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("alert_rules.id", ondelete="CASCADE"),
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[int] = mapped_column(Integer, default=_now)
    updated_at: Mapped[int] = mapped_column(
        Integer, default=_now, onupdate=_now)

    rule: Mapped["AlertRule"] = relationship(
        "AlertRule", back_populates="history")

    __table_args__ = (
        Index("ix_alert_history_server_rule", "server_uuid", "rule_id"),
    )


# ─── Logs ─────────────────────────────────────────────────────────────────────

class Log(Base):
    """系统级/安全审计日志记录."""

    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True)
    ip: Mapped[str | None] = mapped_column(String)
    uuid: Mapped[str | None] = mapped_column(String)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    msg_type: Mapped[str] = mapped_column(String, nullable=False)
    time: Mapped[int] = mapped_column(Integer, nullable=False, default=_now)

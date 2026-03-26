"""DNS 与 DDNS 相关的 SQLAlchemy 模型.

四张核心表:
  dns_credentials        DNS 厂商凭证
  dns_domains            托管域名
  dns_records            解析记录（本地缓存）
  ddns_tasks             DDNS 自动化任务
"""

import time

import sqlalchemy as sa
from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base


# ─── helpers ──────────────────────────────────────────────────────────────────

def _now() -> int:
    return int(time.time())


# ─── DNS Credential ──────────────────────────────────────────────────────────

class DnsCredential(Base):
    """DNS 厂商凭证 — 存储加密后的认证信息."""

    __tablename__ = "dns_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    credentials: Mapped[str] = mapped_column(Text, nullable=False)
    is_valid: Mapped[int] = mapped_column(
        Integer, default=1, server_default=sa.text("1"),
    )
    created_at: Mapped[int] = mapped_column(Integer, default=_now)
    updated_at: Mapped[int] = mapped_column(Integer, default=_now, onupdate=_now)

    # 关系
    domains: Mapped[list["DnsDomain"]] = relationship(
        "DnsDomain", back_populates="credential",
    )


# ─── DNS Domain ──────────────────────────────────────────────────────────────

class DnsDomain(Base):
    """托管域名 — 代表用户接入管理的主域名."""

    __tablename__ = "dns_domains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    credential_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("dns_credentials.id", ondelete="SET NULL"),
    )
    domain_name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    zone_id: Mapped[str | None] = mapped_column(String)
    sync_status: Mapped[str] = mapped_column(
        String, default="pending", server_default=sa.text("'pending'"),
    )
    last_sync_at: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer, default=_now)
    updated_at: Mapped[int] = mapped_column(Integer, default=_now, onupdate=_now)

    # 关系
    credential: Mapped["DnsCredential | None"] = relationship(
        "DnsCredential", back_populates="domains",
    )
    records: Mapped[list["DnsRecord"]] = relationship(
        "DnsRecord", back_populates="domain",
        cascade="all, delete-orphan",
    )


# ─── DNS Record ──────────────────────────────────────────────────────────────

class DnsRecord(Base):
    """DNS 解析记录 — 厂商侧记录的本地缓存."""

    __tablename__ = "dns_records"
    __table_args__ = (
        Index("ix_dns_record_domain", "domain_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("dns_domains.id", ondelete="CASCADE"),
        nullable=False,
    )
    record_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    ttl: Mapped[int] = mapped_column(
        Integer, default=600, server_default=sa.text("600"),
    )
    priority: Mapped[int | None] = mapped_column(Integer)
    proxied: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sa.text("0"),
    )
    status: Mapped[str] = mapped_column(
        String, default="active", server_default=sa.text("'active'"),
    )
    synced_at: Mapped[int | None] = mapped_column(Integer)

    # 关系
    domain: Mapped["DnsDomain"] = relationship(
        "DnsDomain", back_populates="records",
    )
    ddns_task: Mapped["DdnsTask | None"] = relationship(
        "DdnsTask", back_populates="record", uselist=False,
        cascade="all, delete-orphan",
    )


# ─── DDNS Task ───────────────────────────────────────────────────────────────

class DdnsTask(Base):
    """DDNS 自动化任务 — 将监控节点 IP 自动推送到 DNS 记录."""

    __tablename__ = "ddns_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("dns_records.id", ondelete="CASCADE"),
        unique=True,
    )
    server_uuid: Mapped[str] = mapped_column(
        String,
        ForeignKey("servers.uuid", ondelete="CASCADE"),
    )
    ip_version: Mapped[str] = mapped_column(
        String, default="ipv4", server_default=sa.text("'ipv4'"),
    )
    last_ip: Mapped[str | None] = mapped_column(String)
    is_active: Mapped[int] = mapped_column(
        Integer, default=1, server_default=sa.text("1"),
    )
    last_updated: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    error_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sa.text("0"),
    )
    created_at: Mapped[int] = mapped_column(Integer, default=_now)

    # 关系
    record: Mapped["DnsRecord"] = relationship(
        "DnsRecord", back_populates="ddns_task",
    )

"""客户端与节点管理相关的 SQLAlchemy 模型.

对应数据库文档 § 2 — Clients:
  servers, server_status, groups, server_groups
"""

import time
import uuid as _uuid

from sqlalchemy import (
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base


# ─── helpers ──────────────────────────────────────────────────────────────────

def _gen_uuid() -> str:
    return str(_uuid.uuid4())


def _now() -> int:
    return int(time.time())


# ─── Servers ──────────────────────────────────────────────────────────────────

class Server(Base):
    """核心服务器配置表 — 由控制面板管理的静态配置."""

    __tablename__ = "servers"

    uuid: Mapped[str] = mapped_column(
        String, primary_key=True, default=_gen_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    token: Mapped[str | None] = mapped_column(
        String, unique=True, index=True)

    # ── 硬件信息（Agent 上报） ────────────────────────────
    cpu_name: Mapped[str | None] = mapped_column(String)
    virtualization: Mapped[str | None] = mapped_column(String)
    arch: Mapped[str | None] = mapped_column(String)
    cpu_cores: Mapped[int | None] = mapped_column(Integer)
    os: Mapped[str | None] = mapped_column(String)
    kernel_version: Mapped[str | None] = mapped_column(String)

    # ── 网络 & 位置 ──────────────────────────────────────
    ipv4: Mapped[str | None] = mapped_column(String)
    ipv6: Mapped[str | None] = mapped_column(String)
    region: Mapped[str | None] = mapped_column(String)

    # ── 容量信息 ─────────────────────────────────────────
    mem_total: Mapped[int | None] = mapped_column(Integer)
    swap_total: Mapped[int | None] = mapped_column(Integer)
    disk_total: Mapped[int | None] = mapped_column(Integer)

    # ── 其他 ─────────────────────────────────────────────
    version: Mapped[str | None] = mapped_column(String)
    remark: Mapped[str | None] = mapped_column(Text)

    # ── 前端控制 ─────────────────────────────────────────
    top: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    hidden: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    is_approved: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    enable_statistics_mode: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))

    created_at: Mapped[int] = mapped_column(Integer, default=_now)

    # ── 关系 ─────────────────────────────────────────────
    status: Mapped["ServerStatus | None"] = relationship(
        "ServerStatus", back_populates="server", uselist=False,
        cascade="all, delete-orphan",
    )
    group_links: Mapped[list["ServerGroup"]] = relationship(
        "ServerGroup", back_populates="server",
        cascade="all, delete-orphan",
    )
    billing_rule: Mapped["ServerBillingRule | None"] = relationship(
        "ServerBillingRule", uselist=False,
        cascade="all, delete-orphan",
    )


# ─── Server Status ────────────────────────────────────────────────────────────

class ServerStatus(Base):
    """服务器实时状态表 — 记录当前运行状态和最后在线时间."""

    __tablename__ = "server_status"

    uuid: Mapped[str] = mapped_column(
        String, ForeignKey("servers.uuid", ondelete="CASCADE"),
        primary_key=True,
    )
    status: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    last_online: Mapped[int | None] = mapped_column(Integer)
    current_run_id: Mapped[str | None] = mapped_column(String)
    boot_time: Mapped[int | None] = mapped_column(Integer)
    total_flow_out: Mapped[int | None] = mapped_column(Integer)
    total_flow_in: Mapped[int | None] = mapped_column(Integer)

    server: Mapped["Server"] = relationship(
        "Server", back_populates="status")


# ─── Groups ───────────────────────────────────────────────────────────────────

class Group(Base):
    """服务器分组表 — 将服务器划分为不同逻辑组."""

    __tablename__ = "groups"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=_gen_uuid)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    top: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer, default=_now)

    server_links: Mapped[list["ServerGroup"]] = relationship(
        "ServerGroup", back_populates="group",
        cascade="all, delete-orphan",
    )


# ─── Server ↔ Group 中间表 ────────────────────────────────────────────────────

class ServerGroup(Base):
    """服务器与分组的多对多关联."""

    __tablename__ = "server_groups"

    server_uuid: Mapped[str] = mapped_column(
        String,
        ForeignKey("servers.uuid", ondelete="CASCADE"),
        primary_key=True,
    )
    group_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("groups.id", ondelete="CASCADE"),
        primary_key=True,
    )

    server: Mapped["Server"] = relationship(
        "Server", back_populates="group_links")
    group: Mapped["Group"] = relationship(
        "Group", back_populates="server_links")


# ─── Server Billing Rules ─────────────────────────────────────────────────────

class ServerBillingRule(Base):
    """服务器计费规则表 — 定义计费策略和流量计算方式."""

    __tablename__ = "server_billing_rules"

    uuid: Mapped[str] = mapped_column(
        String, ForeignKey("servers.uuid", ondelete="CASCADE"),
        primary_key=True,
    )
    billing_cycle: Mapped[int | None] = mapped_column(Integer)
    billing_cycle_data: Mapped[int | None] = mapped_column(Integer)
    billing_cycle_cost: Mapped[float | None] = mapped_column(Float)
    traffic_reset_day: Mapped[int | None] = mapped_column(Integer)
    traffic_threshold: Mapped[int | None] = mapped_column(Integer)
    accounting_mode: Mapped[int | None] = mapped_column(Integer)
    billing_cycle_cost_code: Mapped[str | None] = mapped_column(String)
    expiry_date: Mapped[int | None] = mapped_column(Integer)

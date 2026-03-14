"""网络监控目标与探测结果的 SQLAlchemy 模型.

对应数据库文档 — Network Monitoring:
  network_targets          网络监控目标
  network_target_dispatch  探测任务下发节点
  network_status           网络探测结果
"""

import sqlalchemy as sa
from sqlalchemy import ForeignKey, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class NetworkTarget(Base):
    """网络监控目标 — 定义需要探测的主机及协议."""

    __tablename__ = "network_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    host: Mapped[str] = mapped_column(String, nullable=False)
    protocol: Mapped[str] = mapped_column(
        String, default="icmp", server_default=sa.text("'icmp'"),
    )
    port: Mapped[int | None] = mapped_column(Integer)
    interval: Mapped[int] = mapped_column(
        Integer, default=60, server_default=sa.text("60"),
    )
    enabled: Mapped[int] = mapped_column(
        Integer, default=1, server_default=sa.text("1"),
    )


class NetworkTargetDispatch(Base):
    """探测任务下发节点 — 定义哪些 Agent 执行哪些目标的探测.

    复合主键: (target_id, node_type, node_id)
    """

    __tablename__ = "network_target_dispatch"

    target_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("network_targets.id", ondelete="CASCADE"),
        primary_key=True,
    )
    node_type: Mapped[str] = mapped_column(String, primary_key=True)
    node_id: Mapped[str] = mapped_column(String, primary_key=True)
    is_exclude: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sa.text("0"),
    )


class NetworkStatus(Base):
    """网络探测结果 — 记录每个节点对每个目标的探测数据.

    复合主键: (target_id, server_uuid, time)
    """

    __tablename__ = "network_status"

    target_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("network_targets.id", ondelete="CASCADE"),
        primary_key=True,
    )
    server_uuid: Mapped[str] = mapped_column(
        String,
        ForeignKey("servers.uuid", ondelete="CASCADE"),
        primary_key=True,
    )
    time: Mapped[int] = mapped_column(Integer, primary_key=True)
    median_latency: Mapped[float | None] = mapped_column(Float)
    max_latency: Mapped[float | None] = mapped_column(Float)
    min_latency: Mapped[float | None] = mapped_column(Float)
    packet_loss: Mapped[float] = mapped_column(
        Float, default=0, server_default=sa.text("0"),
    )

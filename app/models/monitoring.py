"""系统资源监控数据的 SQLAlchemy 模型.

对应数据库文档 § 3 — Monitoring & Records:
  load_now
"""

from sqlalchemy import (
    ForeignKey,
    Integer,
    String,
    Float,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class LoadNow(Base):
    """系统资源监控数据 — 记录服务器最近 1 分钟的资源使用情况.

    复合主键: (server_uuid, time)
    """

    __tablename__ = "load_now"

    server_uuid: Mapped[str] = mapped_column(
        String,
        ForeignKey("servers.uuid", ondelete="CASCADE"),
        primary_key=True,
    )
    time: Mapped[int] = mapped_column(Integer, primary_key=True)

    # ── CPU ──
    cpu: Mapped[float | None] = mapped_column(Float)

    # ── 内存 ──
    ram: Mapped[int | None] = mapped_column(Integer)
    ram_total: Mapped[int | None] = mapped_column(Integer)

    # ── Swap ──
    swap: Mapped[int | None] = mapped_column(Integer)
    swap_total: Mapped[int | None] = mapped_column(Integer)

    # ── 系统负载 ──
    load: Mapped[float | None] = mapped_column(Float)

    # ── 磁盘 ──
    disk: Mapped[int | None] = mapped_column(Integer)
    disk_total: Mapped[int | None] = mapped_column(Integer)

    # ── 网络 ──
    net_in: Mapped[int | None] = mapped_column(Integer)
    net_out: Mapped[int | None] = mapped_column(Integer)

    # ── 连接 & 进程 ──
    tcp: Mapped[int | None] = mapped_column(Integer)
    udp: Mapped[int | None] = mapped_column(Integer)
    process: Mapped[int | None] = mapped_column(Integer)

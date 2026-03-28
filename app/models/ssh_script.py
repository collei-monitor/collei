"""SSH 快捷脚本库相关的 SQLAlchemy 模型.

对应数据库文档 — SSH Scripts:
  ssh_scripts    全局快捷脚本库表
"""

import time

from sqlalchemy import Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


# ─── helpers ──────────────────────────────────────────────────────────────────

def _now() -> int:
    return int(time.time())


# ─── SSH Scripts ──────────────────────────────────────────────────────────────

class SshScript(Base):
    """SSH 快捷脚本库 — 全局通用的脚本片段，可在 Web SSH 终端中一键执行."""

    __tablename__ = "ssh_scripts"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(
        String, default="bash", server_default=text("'bash'"),
    )
    top: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"),
    )
    created_at: Mapped[int] = mapped_column(Integer, default=_now)
    updated_at: Mapped[int] = mapped_column(Integer, default=_now)

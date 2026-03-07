"""系统全局配置相关的 SQLAlchemy 模型.

对应数据库文档 § 5 — Configs & Misc:
  configs
"""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class Config(Base):
    """系统全局设定的键值对存储库."""

    __tablename__ = "configs"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)

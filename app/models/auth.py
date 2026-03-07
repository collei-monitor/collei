"""认证与用户管理相关的 SQLAlchemy 模型.

对应数据库文档 § 1 — Auth & Users:
  users, sessions, login_attempts, oauth_states, oidc
"""

import time
import uuid as _uuid

from sqlalchemy import (
    ForeignKey,
    Index,
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


# ─── Users ────────────────────────────────────────────────────────────────────

class User(Base):
    """系统管理员用户账号."""

    __tablename__ = "users"

    uuid: Mapped[str] = mapped_column(
        String, primary_key=True, default=_gen_uuid)
    username: Mapped[str] = mapped_column(String, unique=True, index=True)
    passwd: Mapped[str] = mapped_column(String)
    sso_type: Mapped[str | None] = mapped_column(String)
    sso_id: Mapped[str | None] = mapped_column(String)
    two_factor: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[int] = mapped_column(Integer, default=_now)
    updated_at: Mapped[int] = mapped_column(
        Integer, default=_now, onupdate=_now)
    sessions: Mapped[list["Session"]] = relationship(
        "Session", back_populates="user", cascade="all, delete-orphan"
    )


# ─── Sessions ─────────────────────────────────────────────────────────────────

class Session(Base):
    """用户登录会话."""

    __tablename__ = "sessions"

    session: Mapped[str] = mapped_column(String, primary_key=True)
    uuid: Mapped[str] = mapped_column(String, ForeignKey(
        "users.uuid", ondelete="CASCADE"), index=True)
    user_agent: Mapped[str | None] = mapped_column(String)
    ip: Mapped[str | None] = mapped_column(String)
    login_method: Mapped[str | None] = mapped_column(String)
    latest_online: Mapped[int | None] = mapped_column(Integer)
    latest_user_agent: Mapped[str | None] = mapped_column(String)
    latest_ip: Mapped[str | None] = mapped_column(String)
    expires: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer, default=_now)
    user: Mapped["User"] = relationship("User", back_populates="sessions")


# ─── Login Attempts ───────────────────────────────────────────────────────────

class LoginAttempt(Base):
    """登录尝试记录 — 用于防暴力破解."""

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True)
    ip_address: Mapped[str] = mapped_column(String)
    username: Mapped[str | None] = mapped_column(String)
    attempt_type: Mapped[str | None] = mapped_column(String)
    success: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    timestamp: Mapped[int] = mapped_column(Integer, default=_now)

    __table_args__ = (
        # 查询某 IP 在时间窗口内的失败次数
        Index("ix_login_attempts_ip_ts", "ip_address", "timestamp"),
    )


# ─── OAuth States ─────────────────────────────────────────────────────────────

class OAuthState(Base):
    """OAuth 授权流程的临时 state 令牌，防 CSRF."""

    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[int] = mapped_column(Integer, default=_now)
    expires_at: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(
        String, default="login", server_default=text("'login'"))
    uuid: Mapped[str | None] = mapped_column(String)


# ─── OIDC Providers ──────────────────────────────────────────────────────────

class OIDCProvider(Base):
    """OIDC 第三方单点登录服务提供商配置."""

    __tablename__ = "oidc"

    name: Mapped[str] = mapped_column(String, primary_key=True, unique=True)
    addition: Mapped[str | None] = mapped_column(Text)  # JSON

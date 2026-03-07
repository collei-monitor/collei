"""认证与用户管理的 CRUD / DAO 操作."""

from __future__ import annotations

import time
from typing import Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.models.auth import (
    LoginAttempt,
    OAuthState,
    OIDCProvider,
    Session,
    User,
)


# ═══════════════════════════════════════════════════════════════════════════════
# User
# ═══════════════════════════════════════════════════════════════════════════════

async def get_user_by_uuid(db: AsyncSession, uuid: str) -> User | None:
    result = await db.execute(select(User).where(User.uuid == uuid))
    return result.scalar_one_or_none()


async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def create_user(
    db: AsyncSession,
    *,
    username: str,
    passwd_hash: str,
    sso_type: str | None = None,
    sso_id: str | None = None,
) -> User:
    user = User(username=username, passwd=passwd_hash, sso_type=sso_type, sso_id=sso_id)
    db.add(user)
    await db.flush()
    return user


async def update_user(db: AsyncSession, uuid: str, **kwargs) -> User | None:
    kwargs["updated_at"] = int(time.time())
    await db.execute(update(User).where(User.uuid == uuid).values(**kwargs))
    await db.flush()
    return await get_user_by_uuid(db, uuid)


async def get_all_users(db: AsyncSession) -> Sequence[User]:
    result = await db.execute(select(User).order_by(User.created_at))
    return result.scalars().all()


# ═══════════════════════════════════════════════════════════════════════════════
# Session
# ═══════════════════════════════════════════════════════════════════════════════

async def create_session(
    db: AsyncSession,
    *,
    session_token: str,
    uuid: str,
    user_agent: str | None = None,
    ip: str | None = None,
    login_method: str = "password",
    expires: int,
) -> Session:
    sess = Session(
        session=session_token,
        uuid=uuid,
        user_agent=user_agent,
        ip=ip,
        login_method=login_method,
        latest_online=int(time.time()),
        latest_user_agent=user_agent,
        latest_ip=ip,
        expires=expires,
    )
    db.add(sess)
    await db.flush()
    return sess


async def get_session(db: AsyncSession, session_token: str) -> Session | None:
    result = await db.execute(select(Session).where(Session.session == session_token))
    return result.scalar_one_or_none()


async def touch_session(
    db: AsyncSession,
    session_token: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """更新会话的最后活跃信息."""
    values: dict = {"latest_online": int(time.time())}
    if ip:
        values["latest_ip"] = ip
    if user_agent:
        values["latest_user_agent"] = user_agent
    await db.execute(update(Session).where(Session.session == session_token).values(**values))


async def delete_session(db: AsyncSession, session_token: str) -> None:
    await db.execute(delete(Session).where(Session.session == session_token))


async def delete_user_sessions(db: AsyncSession, uuid: str) -> None:
    await db.execute(delete(Session).where(Session.uuid == uuid))


async def get_user_sessions(db: AsyncSession, uuid: str) -> Sequence[Session]:
    result = await db.execute(
        select(Session).where(Session.uuid == uuid).order_by(Session.created_at.desc())
    )
    return result.scalars().all()


async def cleanup_expired_sessions(db: AsyncSession) -> int:
    """清理已过期会话，返回删除数量."""
    now = int(time.time())
    result = await db.execute(delete(Session).where(Session.expires < now))
    return result.rowcount  # type: ignore[return-value]


# ═══════════════════════════════════════════════════════════════════════════════
# Login Attempts
# ═══════════════════════════════════════════════════════════════════════════════

async def record_login_attempt(
    db: AsyncSession,
    *,
    ip_address: str,
    username: str | None = None,
    attempt_type: str = "password",
    success: bool = False,
) -> None:
    attempt = LoginAttempt(
        ip_address=ip_address,
        username=username,
        attempt_type=attempt_type,
        success=1 if success else 0,
    )
    db.add(attempt)
    await db.flush()


async def count_failed_attempts(db: AsyncSession, ip_address: str) -> int:
    """统计当前窗口期内该 IP 的登录失败次数."""
    since = int(time.time()) - settings.LOGIN_ATTEMPT_WINDOW
    result = await db.execute(
        select(LoginAttempt)
        .where(
            LoginAttempt.ip_address == ip_address,
            LoginAttempt.success == 0,
            LoginAttempt.timestamp >= since,
        )
    )
    return len(result.scalars().all())


# ═══════════════════════════════════════════════════════════════════════════════
# OAuth States
# ═══════════════════════════════════════════════════════════════════════════════

async def create_oauth_state(
    db: AsyncSession,
    *,
    state: str,
    expires_at: int,
    type_: str = "login",
    uuid: str | None = None,
) -> OAuthState:
    obj = OAuthState(state=state, expires_at=expires_at, type=type_, uuid=uuid)
    db.add(obj)
    await db.flush()
    return obj


async def get_and_delete_oauth_state(db: AsyncSession, state: str) -> OAuthState | None:
    """获取并删除 OAuth state（一次性使用）."""
    result = await db.execute(select(OAuthState).where(OAuthState.state == state))
    obj = result.scalar_one_or_none()
    if obj:
        await db.delete(obj)
        await db.flush()
    return obj


async def cleanup_expired_oauth_states(db: AsyncSession) -> int:
    now = int(time.time())
    result = await db.execute(delete(OAuthState).where(OAuthState.expires_at < now))
    return result.rowcount  # type: ignore[return-value]


# ═══════════════════════════════════════════════════════════════════════════════
# OIDC Providers
# ═══════════════════════════════════════════════════════════════════════════════

async def get_oidc_provider(db: AsyncSession, name: str) -> OIDCProvider | None:
    result = await db.execute(select(OIDCProvider).where(OIDCProvider.name == name))
    return result.scalar_one_or_none()


async def get_all_oidc_providers(db: AsyncSession) -> Sequence[OIDCProvider]:
    result = await db.execute(select(OIDCProvider).order_by(OIDCProvider.name))
    return result.scalars().all()


async def upsert_oidc_provider(db: AsyncSession, *, name: str, addition: str | None = None) -> OIDCProvider:
    existing = await get_oidc_provider(db, name)
    if existing:
        existing.addition = addition
        await db.flush()
        return existing
    provider = OIDCProvider(name=name, addition=addition)
    db.add(provider)
    await db.flush()
    return provider


async def delete_oidc_provider(db: AsyncSession, name: str) -> bool:
    result = await db.execute(delete(OIDCProvider).where(OIDCProvider.name == name))
    return (result.rowcount or 0) > 0

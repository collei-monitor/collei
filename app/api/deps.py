"""FastAPI 依赖注入 — 认证与当前用户."""

from __future__ import annotations

import time

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decode_access_token
from app.crud import auth as auth_crud
from app.db.session import get_async_session
from app.models.auth import User

_bearer_scheme = HTTPBearer(auto_error=False)


def _extract_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> str | None:
    """从 Bearer Header 或 HttpOnly Cookie 中提取 JWT token.

    优先级: Authorization Header > Cookie
    """
    if credentials:
        return credentials.credentials
    return request.cookies.get(settings.COOKIE_NAME)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_async_session),
) -> User:
    """从 Bearer token 或 HttpOnly Cookie 中解析并验证当前用户.

    验证流程:
      1. 从 Header 或 Cookie 提取 token
      2. 解码 JWT
      3. 检查 session 是否存在且未过期
      4. 返回用户对象 & 更新 session 活跃信息
    """
    raw_token = _extract_token(request, credentials)
    if raw_token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    payload = decode_access_token(raw_token)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    user_uuid: str = payload.get("sub", "")
    session_id: str = payload.get("session", "")

    # 验证 session
    session = await auth_crud.get_session(db, session_id)
    if session is None or session.expires < int(time.time()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or revoked")

    # 验证用户
    user = await auth_crud.get_user_by_uuid(db, user_uuid)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    # 异步刷新 session 活跃信息
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    await auth_crud.touch_session(db, session_id, ip=client_ip, user_agent=user_agent)

    return user


async def get_optional_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_async_session),
) -> User | None:
    """可选认证：有有效 token 则返回用户对象，否则返回 None（不抛出异常）."""
    raw_token = _extract_token(request, credentials)
    if raw_token is None:
        return None

    payload = decode_access_token(raw_token)
    if payload is None:
        return None

    user_uuid: str = payload.get("sub", "")
    session_id: str = payload.get("session", "")

    session = await auth_crud.get_session(db, session_id)
    if session is None or session.expires < int(time.time()):
        return None

    user = await auth_crud.get_user_by_uuid(db, user_uuid)
    if user is None:
        return None

    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    await auth_crud.touch_session(db, session_id, ip=client_ip, user_agent=user_agent)

    return user


def get_client_ip(request: Request) -> str:
    """获取请求的客户端 IP（支持反向代理 X-Forwarded-For）."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

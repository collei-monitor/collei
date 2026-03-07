"""认证与用户管理 API 路由.

端点:
  POST   /auth/login           密码登录
  POST   /auth/login/2fa       二阶段登录提交 TOTP
  POST   /auth/logout          退出登录（删除当前会话）
  GET    /auth/me               获取当前用户信息
  PUT    /auth/me               更新当前用户信息
  GET    /auth/sessions         获取当前用户的所有会话
  DELETE /auth/sessions/{sid}   删除指定会话
  POST   /auth/2fa/setup        开启 2FA（生成密钥）
  POST   /auth/2fa/verify       验证并激活 2FA
  DELETE /auth/2fa              关闭 2FA
  GET    /auth/oidc             获取 OIDC 提供商列表
  POST   /auth/oidc             创建/更新 OIDC 提供商
  DELETE /auth/oidc/{name}      删除 OIDC 提供商
"""

from __future__ import annotations

import time
from typing import Union

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_client_ip, get_current_user
from app.core.config import settings
from app.core.security import (
    create_access_token,
    generate_session_token,
    generate_totp_secret,
    get_totp_uri,
    hash_password,
    verify_password,
    verify_totp,
)
from app.crud import auth as crud
from app.db.session import get_async_session
from app.models.auth import User
from app.schemas.auth import (
    Login2FARequest,
    Login2FARequiredResponse,
    LoginRequest,
    MessageResponse,
    OIDCProviderCreate,
    OIDCProviderRead,
    SessionRead,
    TokenResponse,
    TwoFactorSetupResponse,
    TwoFactorVerifyRequest,
    UserRead,
    UserUpdate,
)

router = APIRouter(prefix="/auth", tags=["auth"])


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _user_to_read(user: User) -> UserRead:
    return UserRead(
        uuid=user.uuid,
        username=user.username,
        sso_type=user.sso_type,
        two_factor_enabled=_get_active_2fa_secret(user) is not None,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def _get_active_2fa_secret(user: User) -> str | None:
    secret = user.two_factor
    if not secret or secret.startswith("pending:"):
        return None
    return secret


async def _issue_token_for_user(
    db: AsyncSession,
    user: User,
    request: Request,
    *,
    login_method: str,
) -> TokenResponse:
    session_token = generate_session_token()
    expires = int(time.time()) + settings.SESSION_EXPIRE_DAYS * 86400
    user_agent = request.headers.get("user-agent")
    client_ip = get_client_ip(request)

    await crud.create_session(
        db,
        session_token=session_token,
        uuid=user.uuid,
        user_agent=user_agent,
        ip=client_ip,
        login_method=login_method,
        expires=expires,
    )

    access_token = create_access_token(
        user_uuid=user.uuid,
        session_id=session_token,
        expires_delta=expires - int(time.time()),
    )
    return TokenResponse(access_token=access_token)


# ── 登录 / 登出 ──────────────────────────────────────────────────────────────

@router.post("/login", response_model=Union[TokenResponse, Login2FARequiredResponse])
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_session),
):
    """密码登录接口，支持防暴力破解 & 2FA."""
    client_ip = get_client_ip(request)

    # 1) 防暴力破解检查
    failed_count = await crud.count_failed_attempts(db, client_ip)
    if failed_count >= settings.LOGIN_ATTEMPT_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please try again later.",
        )

    # 2) 验证用户名 & 密码
    user = await crud.get_user_by_username(db, body.username)
    if user is None or not verify_password(body.password, user.passwd):
        await crud.record_login_attempt(db, ip_address=client_ip, username=body.username, success=False)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # 3) 2FA 检查（支持两阶段登录）
    two_factor_secret = _get_active_2fa_secret(user)
    if two_factor_secret:
        if body.totp_code:
            if not verify_totp(two_factor_secret, body.totp_code):
                await crud.record_login_attempt(
                    db,
                    ip_address=client_ip,
                    username=body.username,
                    attempt_type="2fa",
                    success=False,
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid 2FA code",
                )

            token = await _issue_token_for_user(
                db,
                user,
                request,
                login_method="password+2fa",
            )
            await crud.record_login_attempt(
                db,
                ip_address=client_ip,
                username=body.username,
                attempt_type="2fa",
                success=True,
            )
            return token

        challenge = generate_session_token()
        await crud.create_oauth_state(
            db,
            state=challenge,
            expires_at=int(time.time()) +
            settings.LOGIN_2FA_CHALLENGE_EXPIRE_SECONDS,
            type_="login_2fa",
            uuid=user.uuid,
        )
        return Login2FARequiredResponse(
            login_challenge=challenge,
            expires_in=settings.LOGIN_2FA_CHALLENGE_EXPIRE_SECONDS,
        )

    # 4) 创建会话 & Token
    token = await _issue_token_for_user(db, user, request, login_method="password")

    # 5) 记录成功登录
    await crud.record_login_attempt(
        db,
        ip_address=client_ip,
        username=body.username,
        attempt_type="password",
        success=True,
    )

    return token


@router.post("/login/2fa", response_model=TokenResponse)
async def login_with_2fa(
    body: Login2FARequest,
    request: Request,
    db: AsyncSession = Depends(get_async_session),
):
    """二阶段登录：提交 challenge + TOTP 以完成登录."""
    client_ip = get_client_ip(request)

    failed_count = await crud.count_failed_attempts(db, client_ip)
    if failed_count >= settings.LOGIN_ATTEMPT_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please try again later.",
        )

    challenge = await crud.get_and_delete_oauth_state(db, body.login_challenge)
    now = int(time.time())
    if (
        challenge is None
        or challenge.type != "login_2fa"
        or challenge.expires_at < now
        or not challenge.uuid
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired login challenge",
        )

    user = await crud.get_user_by_uuid(db, challenge.uuid)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    two_factor_secret = _get_active_2fa_secret(user)
    if not two_factor_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA not enabled",
        )

    if not verify_totp(two_factor_secret, body.totp_code):
        await crud.record_login_attempt(
            db,
            ip_address=client_ip,
            username=user.username,
            attempt_type="2fa",
            success=False,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid 2FA code",
        )

    token = await _issue_token_for_user(
        db,
        user,
        request,
        login_method="password+2fa",
    )
    await crud.record_login_attempt(
        db,
        ip_address=client_ip,
        username=user.username,
        attempt_type="2fa",
        success=True,
    )
    return token


@router.post("/logout", response_model=MessageResponse)
async def logout(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
    request: Request = None,  # type: ignore[assignment]
):
    """退出当前会话."""
    # 从 Authorization header 中重新解析 session id
    from app.core.security import decode_access_token

    auth_header = request.headers.get("authorization", "")
    token = auth_header.replace("Bearer ", "").strip()
    payload = decode_access_token(token)
    if payload and "session" in payload:
        await crud.delete_session(db, payload["session"])

    return MessageResponse(message="Logged out successfully")


# ── 当前用户 ──────────────────────────────────────────────────────────────────

@router.get("/me", response_model=UserRead)
async def get_me(current_user: User = Depends(get_current_user)):
    """获取当前登录用户信息."""
    return _user_to_read(current_user)


@router.put("/me", response_model=UserRead)
async def update_me(
    body: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """更新当前用户信息（用户名 / 密码）."""
    update_data: dict = {}
    if body.username is not None:
        existing = await crud.get_user_by_username(db, body.username)
        if existing and existing.uuid != current_user.uuid:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Username already taken")
        update_data["username"] = body.username
    if body.password is not None:
        update_data["passwd"] = hash_password(body.password)

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    updated = await crud.update_user(db, current_user.uuid, **update_data)
    return _user_to_read(updated)  # type: ignore[arg-type]


# ── 会话管理 ──────────────────────────────────────────────────────────────────

@router.get("/sessions", response_model=list[SessionRead])
async def list_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """列出当前用户的所有活跃会话."""
    sessions = await crud.get_user_sessions(db, current_user.uuid)
    return sessions


@router.delete("/sessions/{session_id}", response_model=MessageResponse)
async def revoke_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """撤销指定会话."""
    session = await crud.get_session(db, session_id)
    if session is None or session.uuid != current_user.uuid:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    await crud.delete_session(db, session_id)
    return MessageResponse(message="Session revoked")


# ── 2FA (TOTP) ────────────────────────────────────────────────────────────────

@router.post("/2fa/setup", response_model=TwoFactorSetupResponse)
async def setup_2fa(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """生成 2FA 密钥和二维码 URI（尚未激活）."""
    if _get_active_2fa_secret(current_user):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="2FA already enabled")

    secret = generate_totp_secret()
    # 将密钥临时存储 — 标记为 "pending:" 前缀表示未激活
    await crud.update_user(db, current_user.uuid, two_factor=f"pending:{secret}")

    otpauth_url = get_totp_uri(secret, current_user.username)
    return TwoFactorSetupResponse(secret=secret, otpauth_url=otpauth_url)


@router.post("/2fa/verify", response_model=MessageResponse)
async def verify_and_activate_2fa(
    body: TwoFactorVerifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """通过验证码激活 2FA."""
    tf = current_user.two_factor or ""
    if not tf.startswith("pending:"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No pending 2FA setup")

    secret = tf.removeprefix("pending:")
    if not verify_totp(secret, body.totp_code):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid TOTP code")

    await crud.update_user(db, current_user.uuid, two_factor=secret)
    return MessageResponse(message="2FA activated successfully")


@router.delete("/2fa", response_model=MessageResponse)
async def disable_2fa(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """关闭 2FA."""
    if not current_user.two_factor or current_user.two_factor.startswith("pending:"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="2FA not enabled")
    await crud.update_user(db, current_user.uuid, two_factor=None)
    return MessageResponse(message="2FA disabled")


# ── OIDC 提供商管理 ──────────────────────────────────────────────────────────

@router.get("/oidc", response_model=list[OIDCProviderRead])
async def list_oidc_providers(
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await crud.get_all_oidc_providers(db)


@router.post("/oidc", response_model=OIDCProviderRead, status_code=status.HTTP_201_CREATED)
async def create_or_update_oidc(
    body: OIDCProviderCreate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await crud.upsert_oidc_provider(db, name=body.name, addition=body.addition)


@router.delete("/oidc/{name}", response_model=MessageResponse)
async def remove_oidc(
    name: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    deleted = await crud.delete_oidc_provider(db, name)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="OIDC provider not found")
    return MessageResponse(message=f"OIDC provider '{name}' deleted")

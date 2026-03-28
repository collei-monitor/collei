"""SSO 第三方登录路由.

端点:
  GET /auth/sso/providers              公开 — 获取已启用的 SSO 提供商列表
  GET /auth/sso/{provider_name}/login  公开 — 跳转到第三方授权页
  GET /auth/sso/{provider_name}/callback  公开 — 处理第三方回调
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_client_ip
from app.core.alert_engine import alert_engine
from app.core.audit import audit
from app.core.config import settings
from app.core.security import create_access_token, generate_session_token
from app.core.sso_factory import create_sso
from app.crud import auth as crud
from app.db.session import get_async_session
from app.schemas.auth import SSOProviderPublic

router = APIRouter(prefix="/auth/sso", tags=["sso"])


# ── 公开端点：列出可用 SSO 提供商 ────────────────────────────────────────────

@router.get("/providers", response_model=list[SSOProviderPublic])
async def list_sso_providers(
    db: AsyncSession = Depends(get_async_session),
):
    """返回所有已启用的 SSO 提供商（公开，无需认证）."""
    providers = await crud.get_enabled_oidc_providers(db)
    return [
        SSOProviderPublic(
            name=p.name,
            provider_type=p.provider_type,
            display_order=p.display_order,
        )
        for p in providers
    ]


# ── SSO 登录跳转 ─────────────────────────────────────────────────────────────

@router.get("/{provider_name}/login")
async def sso_login(
    provider_name: str,
    request: Request,
    db: AsyncSession = Depends(get_async_session),
):
    """生成 SSO 授权跳转，重定向到第三方登录页."""
    provider = await crud.get_oidc_provider(db, provider_name)
    if provider is None or not provider.enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SSO provider '{provider_name}' not found or disabled",
        )

    # 生成 state 并存储到数据库（一次性消费，防 CSRF）
    state_token = generate_session_token()
    await crud.create_oauth_state(
        db,
        state=state_token,
        expires_at=int(time.time()) + 300,  # 5 分钟有效
        type_="sso",
    )

    # 构建 callback URL
    callback_url = str(request.url_for("sso_callback", provider_name=provider_name))

    sso = create_sso(provider, redirect_uri=callback_url)
    async with sso:
        return await sso.get_login_redirect(state=state_token)


# ── SSO 回调处理 ─────────────────────────────────────────────────────────────

@router.get("/{provider_name}/callback")
async def sso_callback(
    provider_name: str,
    request: Request,
    db: AsyncSession = Depends(get_async_session),
):
    """处理第三方 SSO 回调，验证后为唯一管理员签发 token."""
    # 1) 验证 state（一次性消费）
    state_param = request.query_params.get("state")
    if not state_param:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing state parameter",
        )

    oauth_state = await crud.get_and_delete_oauth_state(db, state_param)
    now = int(time.time())
    if oauth_state is None or oauth_state.type != "sso" or oauth_state.expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired SSO state",
        )

    # 2) 查找 provider 配置
    provider = await crud.get_oidc_provider(db, provider_name)
    if provider is None or not provider.enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SSO provider '{provider_name}' not found or disabled",
        )

    # 3) 通过 fastapi-sso 验证回调并获取用户信息
    callback_url = str(request.url_for("sso_callback", provider_name=provider_name))
    sso = create_sso(provider, redirect_uri=callback_url)
    async with sso:
        openid = await sso.verify_and_process(request)

    if openid is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="SSO authentication failed",
        )

    # 4) 单用户系统：获取第一个（唯一的）管理员用户
    users = await crud.get_all_users(db)
    if not users:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No admin user found",
        )
    user = users[0]

    # 5) 为管理员签发 session + token
    session_token = generate_session_token()
    expires = int(time.time()) + settings.SESSION_EXPIRE_DAYS * 86400
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent")

    await crud.create_session(
        db,
        session_token=session_token,
        uuid=user.uuid,
        user_agent=user_agent,
        ip=client_ip,
        login_method=f"sso:{provider_name}",
        expires=expires,
    )

    access_token = create_access_token(
        user_uuid=user.uuid,
        session_id=session_token,
        expires_delta=expires - int(time.time()),
    )

    # 6) 登录通知
    await alert_engine.notify_login(
        username=user.username,
        ip=client_ip,
        user_agent=user_agent,
        login_method=f"sso:{provider_name}",
    )
    await audit.emit(
        db, msg_type="auth", message="SSO 登录",
        detail=f"provider={provider_name}", ip=client_ip, user_uuid=user.uuid,
    )

    # 7) 构建前端重定向 URL，设置 cookie 并重定向
    from app.crud import config as crud_config
    frontend_url = await crud_config.get_config_value(db, "frontend_url") or "/"

    response = RedirectResponse(url=frontend_url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key=settings.COOKIE_NAME,
        value=access_token,
        max_age=settings.SESSION_EXPIRE_DAYS * 86400,
        path=settings.COOKIE_PATH,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
    )

    # 清除 fastapi-sso 遗留的临时 cookie
    response.delete_cookie("sso_state")
    response.delete_cookie("pkce_code_verifier")

    return response

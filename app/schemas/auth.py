"""认证与用户管理的 Pydantic 请求/响应模型."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── Token ─────────────────────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class Login2FARequiredResponse(BaseModel):
    requires_2fa: bool = True
    login_challenge: str
    expires_in: int = Field(..., gt=0, description="challenge 剩余有效时间（秒）")


class TokenPayload(BaseModel):
    """JWT 载荷."""
    sub: str  # user uuid
    session: str  # session id
    exp: int


# ── Login ─────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1)
    totp_code: str | None = Field(None, description="2FA TOTP 验证码")


class Login2FARequest(BaseModel):
    login_challenge: str = Field(..., min_length=1)
    totp_code: str = Field(..., min_length=6, max_length=6)


# ── User ──────────────────────────────────────────────────────────────────────
class SSOProviderPublic(BaseModel):
    """公开端点返回的 SSO 提供商信息（不含敏感字段）."""
    name: str
    provider_type: str
    display_order: int = 0

    model_config = {"from_attributes": True}

class UserRead(BaseModel):
    """返回给前端的用户信息（脱敏）."""
    uuid: str
    username: str
    sso_type: str | None = None
    two_factor_enabled: bool = False
    created_at: int | None = None
    updated_at: int | None = None
    ws_token: str | None = Field(None, description="WebSocket 连接专用短时效 token（60 秒）")
    global_registration_token: str | None = None
    agent_url: str | None = None
    providers: list[SSOProviderPublic] = Field(default_factory=list, description="已启用的 SSO 登录方式")

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    """用户自行更新个人信息."""
    username: str | None = Field(None, min_length=1, max_length=64)
    password: str | None = Field(None, min_length=6)


# ── Session ───────────────────────────────────────────────────────────────────

class SessionRead(BaseModel):
    session: str
    user_agent: str | None = None
    ip: str | None = None
    login_method: str | None = None
    latest_online: int | None = None
    latest_user_agent: str | None = None
    latest_ip: str | None = None
    expires: int
    created_at: int | None = None

    model_config = {"from_attributes": True}


# ── 2FA ───────────────────────────────────────────────────────────────────────

class TwoFactorSetupResponse(BaseModel):
    secret: str
    otpauth_url: str


class TwoFactorVerifyRequest(BaseModel):
    totp_code: str = Field(..., min_length=6, max_length=6)


# ── OIDC / SSO Provider ────────────────────────────────────────────────────────────────

class OIDCProviderCreate(BaseModel):
    name: str = Field(..., min_length=1)
    provider_type: str = Field(..., min_length=1, description="google/github/microsoft 等")
    client_id: str = Field(..., min_length=1)
    client_secret: str | None = Field(None, min_length=1, description="留空则保留已有密钥（仅更新时有效）")
    enabled: int = Field(1, ge=0, le=1)
    display_order: int = 0
    scope: str | None = Field(None, description="自定义 scope，逗号分隔")
    addition: str | None = None


class OIDCProviderRead(BaseModel):
    name: str
    provider_type: str
    client_id: str
    has_secret: bool = Field(description="是否已配置 client_secret")
    enabled: int = 1
    display_order: int = 0
    scope: str | None = None
    addition: str | None = None


# ── 通用响应 ──────────────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    message: str

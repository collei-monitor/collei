"""SSO 提供商工厂 — 根据数据库配置动态创建 fastapi-sso 实例."""

from __future__ import annotations

from fastapi_sso.sso.discord import DiscordSSO
from fastapi_sso.sso.facebook import FacebookSSO
from fastapi_sso.sso.github import GithubSSO
from fastapi_sso.sso.gitlab import GitlabSSO
from fastapi_sso.sso.google import GoogleSSO
from fastapi_sso.sso.microsoft import MicrosoftSSO
from fastapi_sso.sso.spotify import SpotifySSO
from fastapi_sso.sso.twitter import TwitterSSO
from fastapi_sso.sso.base import SSOBase

from app.core.crypto import decrypt_credential
from app.models.auth import OIDCProvider

# provider_type → fastapi-sso 类的映射
PROVIDER_MAP: dict[str, type[SSOBase]] = {
    "google": GoogleSSO,
    "github": GithubSSO,
    "microsoft": MicrosoftSSO,
    "facebook": FacebookSSO,
    "discord": DiscordSSO,
    "gitlab": GitlabSSO,
    "spotify": SpotifySSO,
    "twitter": TwitterSSO,
}


def get_supported_types() -> list[str]:
    """返回所有支持的 provider type."""
    return sorted(PROVIDER_MAP.keys())


def create_sso(provider: OIDCProvider, redirect_uri: str) -> SSOBase:
    """根据数据库中的 OIDCProvider 配置创建 SSO 实例.

    Raises:
        ValueError: provider_type 不在 PROVIDER_MAP 中.
    """
    sso_cls = PROVIDER_MAP.get(provider.provider_type)
    if sso_cls is None:
        raise ValueError(f"Unsupported SSO provider type: {provider.provider_type}")

    client_secret = decrypt_credential(provider.client_secret)

    kwargs: dict = {
        "client_id": provider.client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "allow_insecure_http": True,  # 开发环境支持 HTTP
    }

    if provider.scope:
        kwargs["scope"] = [s.strip() for s in provider.scope.split(",") if s.strip()]

    return sso_cls(**kwargs)

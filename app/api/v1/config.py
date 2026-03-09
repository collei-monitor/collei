"""系统配置管理 API 路由（需管理员登录）.

端点:
  GET    /config                  获取所有配置项
  GET    /config/ip_db/available  获取可用 IP 数据库列表
  GET    /config/{key}            获取单个配置项
  PUT    /config/{key}            设置配置项（不存在时创建）
  DELETE /config/{key}            删除配置项

特殊配置项:
  ip_db   — IP 归属地数据库，可选值: GeoLite2 / MaxMind
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config_cache import config_cache
from app.core.geoip import DB_FILES, list_available_dbs
from app.crud import config as crud_config
from app.db.session import get_async_session
from app.models.auth import User

router = APIRouter(prefix="/config", tags=["config"])

# 允许通过 API 设置的配置项白名单（防止写入敏感 key）
_WRITABLE_KEYS = {
    "ip_db",
    "global_registration_token",
    "app_name",
    "offline_threshold_seconds",
    "offline_check_interval",
    "load_retain_seconds",
}

# ip_db 的合法值
_VALID_DB_NAMES = set(DB_FILES.keys())


class ConfigItem(BaseModel):
    key: str
    value: str | None


class ConfigValue(BaseModel):
    value: str


# ═══════════════════════════════════════════════════════════════════════════════

@router.get("", response_model=list[ConfigItem])
async def list_configs(
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取所有配置项."""
    configs = await crud_config.get_all_configs(db)
    return [ConfigItem(key=c.key, value=c.value) for c in configs]


@router.get("/ip_db/available", response_model=list[str])
async def get_available_dbs(
    _current_user: User = Depends(get_current_user),
):
    """获取当前可用的 IP 数据库名称列表."""
    return list_available_dbs()


@router.get("/{key}", response_model=ConfigItem)
async def get_config(
    key: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取单个配置项."""
    config = await crud_config.get_config(db, key)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Config key '{key}' not found",
        )
    return ConfigItem(key=config.key, value=config.value)


@router.put("/{key}", response_model=ConfigItem)
async def set_config(
    key: str,
    body: ConfigValue,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """设置配置项（不存在时创建）."""
    if key not in _WRITABLE_KEYS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Config key '{key}' is not writable via API",
        )
    # ip_db 值合法性检查
    if key == "ip_db" and body.value not in _VALID_DB_NAMES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid ip_db value '{body.value}'. Available: {sorted(_VALID_DB_NAMES)}",
        )
    config = await crud_config.set_config(db, key, body.value)
    config_cache.set(key, config.value)
    return ConfigItem(key=config.key, value=config.value)


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_config(
    key: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """删除配置项."""
    if key not in _WRITABLE_KEYS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Config key '{key}' is not writable via API",
        )
    deleted = await crud_config.delete_config(db, key)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Config key '{key}' not found",
        )
    config_cache.delete(key)

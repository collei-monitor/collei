"""系统配置管理 API 路由（需管理员登录）.

端点:
  GET    /config                  获取所有配置项
  GET    /config/ip_db/available  获取可用 IP 数据库列表
  POST   /config/ip_db/test       测试指定 IP 数据库查询结果
  GET    /config/{key}            获取单个配置项
  PUT    /config                  批量设置配置项
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
from app.core.geoip import DB_FILES, _DISPUTED_REMAP, list_available_dbs, lookup_country
from app.crud import config as crud_config
from app.crud.clients import batch_remap_regions
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
    "disputed_territory",
}

# 统一地区标识的合法值
_VALID_DISPUTED_VALUES = {"0", "1"}

# ip_db 的合法值
_VALID_DB_NAMES = set(DB_FILES.keys())


class ConfigItem(BaseModel):
    key: str
    value: str | None


class ConfigValue(BaseModel):
    value: str


class ConfigBatchItem(BaseModel):
    key: str
    value: str


class IpDbTestRequest(BaseModel):
    db_name: str
    ip: str


class IpDbTestResult(BaseModel):
    db_name: str
    ip: str
    country_code: str | None


# ═══════════════════════════════════════════════════════════════════════════════

@router.get("", response_model=list[ConfigItem])
async def list_configs(
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取所有配置项."""
    configs = await crud_config.get_all_configs(db)
    return [ConfigItem(key=c.key, value=c.value) for c in configs]


@router.put("", response_model=list[ConfigItem])
async def set_configs_batch(
    body: list[ConfigBatchItem],
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """批量设置配置项（不存在时创建）.每项应用与单条设置相同的校验规则."""
    # ── 前置全量校验，任意一项不合法则整体拒绝 ──
    for item in body:
        if item.key not in _WRITABLE_KEYS:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Config key '{item.key}' is not writable via API",
            )
        if item.key == "ip_db" and item.value not in _VALID_DB_NAMES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid ip_db value '{item.value}'. Available: {sorted(_VALID_DB_NAMES)}",
            )
        if item.key == "disputed_territory" and item.value not in _VALID_DISPUTED_VALUES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid disputed_territory value '{item.value}'. Must be '0' or '1'",
            )

    # ── 逐项写入 ──
    results: list[ConfigItem] = []
    trigger_remap = False
    for item in body:
        config = await crud_config.set_config(db, item.key, item.value)
        config_cache.set(item.key, config.value)
        if item.key == "disputed_territory" and config.value == "1":
            trigger_remap = True
        results.append(ConfigItem(key=config.key, value=config.value))

    if trigger_remap:
        from app.core.server_cache import server_cache
        await batch_remap_regions(db, _DISPUTED_REMAP)
        server_cache.remap_regions(_DISPUTED_REMAP)

    return results


@router.get("/ip_db/available", response_model=list[str])
async def get_available_dbs(
    _current_user: User = Depends(get_current_user),
):
    """获取当前可用的 IP 数据库名称列表."""
    return list_available_dbs()


@router.post("/ip_db/test", response_model=IpDbTestResult)
async def test_ip_db(
    body: IpDbTestRequest,
    _current_user: User = Depends(get_current_user),
):
    """使用指定的 IP 数据库查询某个 IP 的归属国家代码."""
    if body.db_name not in DB_FILES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown db_name '{body.db_name}'. Available: {sorted(DB_FILES.keys())}",
        )
    if body.db_name not in list_available_dbs():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Database '{body.db_name}' file not found on server",
        )
    country_code = await lookup_country(body.ip, body.db_name)
    return IpDbTestResult(db_name=body.db_name, ip=body.ip, country_code=country_code)


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
    # disputed_territory 值合法性检查
    if key == "disputed_territory" and body.value not in _VALID_DISPUTED_VALUES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid disputed_territory value '{body.value}'. Must be '0' or '1'",
        )
    config = await crud_config.set_config(db, key, body.value)
    config_cache.set(key, config.value)
    # disputed_territory 开启时：批量修改数据库中已有的争议地区代码
    if key == "disputed_territory" and config.value == "1":
        from app.core.server_cache import server_cache
        await batch_remap_regions(db, _DISPUTED_REMAP)
        server_cache.remap_regions(_DISPUTED_REMAP)
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

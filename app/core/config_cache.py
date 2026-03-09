"""应用配置缓存 — 启动时从数据库预加载，API 写操作后同步更新.

用法:
  # 预加载（应用启动时）
  await config_cache.preload(session)

  # 读取
  value = config_cache.get("ip_db")            # 不存在时返回 None
  value = config_cache.get("ip_db", "GeoLite2")  # 带默认值

  # 写入（跟随 CRUD 操作后调用）
  config_cache.set("ip_db", "MaxMind")

  # 删除
  config_cache.delete("ip_db")
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


class ConfigCache:
    """内存配置缓存（asyncio 单事件循环下线程安全）."""

    def __init__(self) -> None:
        self._cache: dict[str, str | None] = {}

    async def preload(self, db: AsyncSession) -> None:
        """从数据库全量加载所有配置项到缓存."""
        from app.crud.config import get_all_configs

        configs = await get_all_configs(db)
        self._cache = {c.key: c.value for c in configs}

    def get(self, key: str, default: str | None = None) -> str | None:
        """从缓存获取配置值；key 不存在时返回 default."""
        return self._cache.get(key, default)

    def set(self, key: str, value: str | None) -> None:
        """更新缓存中的单个配置项."""
        self._cache[key] = value

    def delete(self, key: str) -> None:
        """从缓存中移除配置项."""
        self._cache.pop(key, None)


# 全局单例
config_cache = ConfigCache()

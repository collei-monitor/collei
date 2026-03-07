"""系统配置的 CRUD / DAO 操作."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config import Config


# ═══════════════════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════════════════

async def get_config(db: AsyncSession, key: str) -> Config | None:
    """根据 key 获取配置值."""
    stmt = select(Config).where(Config.key == key)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_config_value(db: AsyncSession, key: str) -> str | None:
    """根据 key 获取配置值（直接返回 value）."""
    config = await get_config(db, key)
    return config.value if config else None


async def set_config(db: AsyncSession, key: str, value: str) -> Config:
    """设置或更新配置值."""
    config = await get_config(db, key)
    if config:
        config.value = value
    else:
        config = Config(key=key, value=value)
        db.add(config)
    await db.commit()
    await db.refresh(config)
    return config


async def delete_config(db: AsyncSession, key: str) -> bool:
    """删除配置."""
    config = await get_config(db, key)
    if not config:
        return False
    await db.delete(config)
    await db.commit()
    return True


async def get_all_configs(db: AsyncSession) -> list[Config]:
    """获取所有配置."""
    stmt = select(Config)
    result = await db.execute(stmt)
    return result.scalars().all()

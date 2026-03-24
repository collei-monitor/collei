"""公开配置接口（无需认证）.

端点:
  GET  /public/custom   获取自定义头部和自定义 Body 配置
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config_cache import config_cache

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/custom")
async def get_custom_config():
    """公开获取自定义头部和自定义 Body 配置（无需认证）."""
    return {
        "custom_headers": config_cache.get("custom_headers", ""),
        "custom_body": config_cache.get("custom_body", ""),
    }

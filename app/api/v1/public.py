"""公开配置接口（无需认证）.

端点:
  GET  /public/custom    获取自定义头部、Body、app_name、favicon_url
  GET  /public/favicon   获取上传的 Favicon 图片
  GET  /public/theme     获取当前激活主题配置
"""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.config_cache import config_cache

router = APIRouter(prefix="/public", tags=["public"])

_DATA_DIR = Path(settings.DATA_DIR)
_THEMES_DIR = _DATA_DIR / "themes"
_MANIFEST_PATH = _THEMES_DIR / "manifest.json"


def _find_favicon() -> Path | None:
    for p in _DATA_DIR.glob("favicon.*"):
        if p.is_file():
            return p
    return None


@router.get("/custom")
async def get_custom_config():
    """公开获取自定义头部、Body、app_name、favicon_url（无需认证）."""
    favicon = _find_favicon()
    return {
        "custom_headers": config_cache.get("custom_headers", ""),
        "custom_body": config_cache.get("custom_body", ""),
        "app_name": config_cache.get("app_name", "Collei"),
        "favicon_url": "/api/v1/public/favicon" if favicon else "",
    }


@router.get("/favicon")
async def get_favicon():
    """返回上传的 Favicon 文件（无需认证）."""
    favicon = _find_favicon()
    if not favicon:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No favicon uploaded")
    media_type = mimetypes.guess_type(str(favicon))[0] or "application/octet-stream"
    return FileResponse(
        str(favicon),
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/theme")
async def get_active_theme():
    """返回当前激活主题的 theme.json 配置（无需认证）."""
    active = config_cache.get("active_theme", "") or "default"

    if active == "default":
        return {
            "id": "default",
            "name": "内置主题",
            "description": "Collei 默认展示页",
            "version": "builtin",
            "author": "Collei",
            "is_builtin": True,
        }

    # 读取 manifest 获取元数据
    manifest_entry = None
    if _MANIFEST_PATH.exists():
        manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        for entry in manifest:
            if entry.get("id") == active:
                manifest_entry = entry
                break

    if not manifest_entry:
        return {
            "id": "default",
            "name": "内置主题",
            "description": "Collei 默认展示页",
            "version": "builtin",
            "author": "Collei",
            "is_builtin": True,
        }

    # 读取主题目录中的 theme.json
    theme_json_path = _THEMES_DIR / active / "theme.json"
    theme_data: dict = {}
    if theme_json_path.exists():
        try:
            theme_data = json.loads(theme_json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    return {
        "id": active,
        "name": theme_data.get("name", manifest_entry.get("name", "")),
        "description": theme_data.get("description", manifest_entry.get("description", "")),
        "version": theme_data.get("version", manifest_entry.get("version", "")),
        "author": theme_data.get("author", manifest_entry.get("author", "")),
        "created_at": manifest_entry.get("created_at", ""),
        "is_builtin": False,
    }

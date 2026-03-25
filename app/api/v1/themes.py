"""主题管理 & Favicon 上传 API（需管理员登录）.

端点:
  GET    /config/themes           列出所有主题（含内置 default）
  POST   /config/themes           上传新主题（ZIP，需包含 theme.json + index.html）
  DELETE /config/themes/{id}      删除指定主题
  PUT    /config/themes/active    切换激活主题
  POST   /config/favicon          上传 Favicon 图片
  DELETE /config/favicon          删除已上传 Favicon
"""

from __future__ import annotations

import json
import mimetypes
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.config_cache import config_cache
from app.crud import config as crud_config
from app.db.session import get_async_session
from app.models.auth import User
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/config", tags=["config"])

DATA_DIR = Path(settings.DATA_DIR)
THEMES_DIR = DATA_DIR / "themes"
MANIFEST_PATH = THEMES_DIR / "manifest.json"

# ── 常量 ──────────────────────────────────────────────────────────────────────
MAX_THEME_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_FAVICON_SIZE = 2 * 1024 * 1024  # 2 MB

_FAVICON_ALLOWED_TYPES = {
    "image/png",
    "image/x-icon",
    "image/vnd.microsoft.icon",
    "image/svg+xml",
    "image/webp",
    "image/gif",
    "image/jpeg",
}

_FAVICON_EXT_MAP = {
    "image/png": ".png",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
    "image/svg+xml": ".svg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
}

# theme.json 必填字段
_REQUIRED_THEME_FIELDS = {"name", "version"}


# ── Pydantic 模型 ─────────────────────────────────────────────────────────────

class ThemeInfo(BaseModel):
    id: str
    name: str
    description: str
    version: str
    author: str
    created_at: str
    file_count: int
    total_size: int
    is_active: bool
    is_builtin: bool


class ActivateThemeRequest(BaseModel):
    theme_id: str


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _read_manifest() -> list[dict]:
    """读取 manifest.json；不存在时返回空列表."""
    if not MANIFEST_PATH.exists():
        return []
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _write_manifest(data: list[dict]) -> None:
    """写入 manifest.json."""
    THEMES_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _dir_stats(directory: Path) -> tuple[int, int]:
    """返回 (文件数, 总字节数)."""
    count = 0
    total = 0
    for f in directory.rglob("*"):
        if f.is_file():
            count += 1
            total += f.stat().st_size
    return count, total


def _find_favicon() -> Path | None:
    """在 data/ 下查找 favicon.* 文件."""
    for p in DATA_DIR.glob("favicon.*"):
        if p.is_file():
            return p
    return None


def _get_active_theme_id() -> str:
    return config_cache.get("active_theme", "") or "default"


# ── 主题 CRUD ─────────────────────────────────────────────────────────────────

@router.get("/themes", response_model=list[ThemeInfo])
async def list_themes(
    _current_user: User = Depends(get_current_user),
):
    """列出所有主题（含内置 default）."""
    active = _get_active_theme_id()
    manifest = _read_manifest()

    result: list[ThemeInfo] = []

    # 内置主题
    result.append(ThemeInfo(
        id="default",
        name="内置主题",
        description="Collei 默认展示页",
        version="builtin",
        author="Collei",
        created_at="",
        file_count=0,
        total_size=0,
        is_active=(active == "default"),
        is_builtin=True,
    ))

    # 用户上传主题
    for entry in manifest:
        theme_dir = THEMES_DIR / entry["id"]
        if not theme_dir.exists():
            continue
        result.append(ThemeInfo(
            id=entry["id"],
            name=entry.get("name", ""),
            description=entry.get("description", ""),
            version=entry.get("version", ""),
            author=entry.get("author", ""),
            created_at=entry.get("created_at", ""),
            file_count=entry.get("file_count", 0),
            total_size=entry.get("total_size", 0),
            is_active=(active == entry["id"]),
            is_builtin=False,
        ))

    return result


@router.post("/themes", response_model=ThemeInfo, status_code=status.HTTP_201_CREATED)
async def upload_theme(
    file: UploadFile,
    _current_user: User = Depends(get_current_user),
):
    """上传新主题 ZIP（须含 theme.json 和 index.html）."""
    # 验证文件类型
    if file.content_type not in ("application/zip", "application/x-zip-compressed"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="仅支持 ZIP 格式文件",
        )

    # 读取文件并验证大小
    data = await file.read()
    if len(data) > MAX_THEME_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件大小超过限制（最大 {MAX_THEME_SIZE // 1024 // 1024} MB）",
        )

    # 验证 ZIP 格式
    import io
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="无效的 ZIP 文件",
        )

    # 安全检查：拒绝包含 .. 或绝对路径的条目
    for name in zf.namelist():
        if ".." in name or name.startswith("/") or name.startswith("\\"):
            zf.close()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"ZIP 包含不安全的路径: {name}",
            )

    # ── 查找根前缀（支持 ZIP 内有一层顶层目录的情况）─────────────────────────
    names = zf.namelist()
    root_prefix = ""
    # 检查是否所有文件都在同一个顶层目录下
    top_level_dirs = {n.split("/")[0] for n in names if "/" in n}
    top_level_files = {n for n in names if "/" not in n and n}
    if len(top_level_dirs) == 1 and not top_level_files:
        root_prefix = top_level_dirs.pop() + "/"

    # 验证 theme.json 存在
    theme_json_path = root_prefix + "theme.json"
    if theme_json_path not in names:
        zf.close()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="ZIP 中缺少 theme.json 文件",
        )

    # 验证 index.html 存在
    index_path = root_prefix + "index.html"
    if index_path not in names:
        zf.close()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="ZIP 中缺少 index.html 文件",
        )

    # 解析 theme.json
    try:
        theme_meta = json.loads(zf.read(theme_json_path))
    except (json.JSONDecodeError, KeyError):
        zf.close()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="theme.json 格式无效",
        )

    # 验证必填字段
    missing = _REQUIRED_THEME_FIELDS - set(theme_meta.keys())
    if missing:
        zf.close()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"theme.json 缺少必填字段: {', '.join(sorted(missing))}",
        )

    # 分配 UUID 并解压
    theme_id = uuid.uuid4().hex[:12]
    theme_dir = THEMES_DIR / theme_id
    theme_dir.mkdir(parents=True, exist_ok=True)

    try:
        for member in zf.namelist():
            if member.endswith("/"):
                continue  # 跳过目录条目
            # 去掉根前缀
            relative = member[len(root_prefix):] if root_prefix else member
            if not relative:
                continue
            target = theme_dir / relative
            # 二次安全校验：确保解压路径不逃逸
            try:
                target.resolve().relative_to(theme_dir.resolve())
            except ValueError:
                shutil.rmtree(theme_dir, ignore_errors=True)
                zf.close()
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"ZIP 包含不安全的路径: {member}",
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(member))
    finally:
        zf.close()

    # 统计文件
    file_count, total_size = _dir_stats(theme_dir)

    # 更新 manifest
    now = datetime.now(timezone.utc).isoformat()
    manifest = _read_manifest()
    entry = {
        "id": theme_id,
        "name": theme_meta.get("name", ""),
        "description": theme_meta.get("description", ""),
        "version": theme_meta.get("version", ""),
        "author": theme_meta.get("author", ""),
        "created_at": now,
        "file_count": file_count,
        "total_size": total_size,
    }
    manifest.append(entry)
    _write_manifest(manifest)

    active = _get_active_theme_id()
    return ThemeInfo(**entry, is_active=(active == theme_id), is_builtin=False)


@router.put("/themes/active")
async def activate_theme(
    body: ActivateThemeRequest,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """切换激活主题."""
    theme_id = body.theme_id

    # 验证目标主题存在
    if theme_id != "default":
        manifest = _read_manifest()
        ids = {e["id"] for e in manifest}
        if theme_id not in ids:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"主题 '{theme_id}' 不存在",
            )
        theme_dir = THEMES_DIR / theme_id
        if not theme_dir.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"主题 '{theme_id}' 目录不存在",
            )

    # 更新 config
    await crud_config.set_config(db, "active_theme", theme_id)
    config_cache.set("active_theme", theme_id)
    return {"active_theme": theme_id}


@router.delete("/themes/{theme_id}")
async def delete_theme(
    theme_id: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """删除指定主题（不可删除内置主题）."""
    if theme_id == "default":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="内置主题不可删除",
        )

    manifest = _read_manifest()
    found = False
    new_manifest = []
    for entry in manifest:
        if entry["id"] == theme_id:
            found = True
        else:
            new_manifest.append(entry)

    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"主题 '{theme_id}' 不存在",
        )

    # 删除目录
    theme_dir = THEMES_DIR / theme_id
    if theme_dir.exists():
        shutil.rmtree(theme_dir)

    # 更新 manifest
    _write_manifest(new_manifest)

    # 若删除的是当前激活主题，回退到 default
    active = _get_active_theme_id()
    if active == theme_id:
        await crud_config.set_config(db, "active_theme", "default")
        config_cache.set("active_theme", "default")

    return {"deleted": theme_id}


# ── Favicon ───────────────────────────────────────────────────────────────────

@router.post("/favicon")
async def upload_favicon(
    file: UploadFile,
    _current_user: User = Depends(get_current_user),
):
    """上传 Favicon 图片（≤2MB，支持常见图片格式）."""
    content_type = file.content_type or ""
    if content_type not in _FAVICON_ALLOWED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"不支持的图片格式: {content_type}",
        )

    data = await file.read()
    if len(data) > MAX_FAVICON_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件大小超过限制（最大 {MAX_FAVICON_SIZE // 1024 // 1024} MB）",
        )

    # 清除旧 favicon
    old = _find_favicon()
    if old:
        old.unlink(missing_ok=True)

    ext = _FAVICON_EXT_MAP.get(content_type, ".png")
    target = DATA_DIR / f"favicon{ext}"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)

    return {"url": "/api/v1/public/favicon"}


@router.delete("/favicon")
async def delete_favicon(
    _current_user: User = Depends(get_current_user),
):
    """删除已上传的 Favicon."""
    old = _find_favicon()
    if old:
        old.unlink(missing_ok=True)
        return {"deleted": True}
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="未找到已上传的 Favicon",
    )

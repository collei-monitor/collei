"""系统备份与恢复 API 路由（需管理员登录）.

端点:
  GET    /system/backup          下载加密备份文件
  POST   /system/restore         上传备份文件进行恢复
  GET    /system/restore/status  查询待恢复状态
  DELETE /system/restore         取消待恢复
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.hashes import SHA256
from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.audit import audit
from app.core.config import settings
from app.db.session import get_async_session
from app.models.auth import User
from app.schemas.system import (
    BackupMeta,
    MessageResponse,
    RestoreResponse,
    RestoreStatusResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])

# ── 常量 ──────────────────────────────────────────────────────────────────────

_MAGIC = b"COLBAK01"                  # 8 字节魔数
_SALT_LEN = 32                         # PBKDF2 盐值
_NONCE_LEN = 12                        # AES-GCM IV
_PBKDF2_ITERATIONS = 600_000           # PBKDF2 迭代次数
_MAX_RESTORE_SIZE = 500 * 1024 * 1024  # 500 MB 上传限制
_BACKUP_VERSION = 1

# 恢复暂存路径
_DATA_DIR = Path(settings.DATA_DIR)
_RESTORE_DIR = _DATA_DIR / ".restore"
_RESTORE_PENDING = _DATA_DIR / ".restore-pending"

# 需要备份的文件（相对于 DATA_DIR）
_REQUIRED_FILES = {"collei.db", ".secrets"}
_OPTIONAL_FILES = {"ssh_ca_key.enc", "ssh_ca_key.pub", "ssh_ca_key_old.pub"}

# 历史监控数据表
_MONITORING_TABLES = ("load_now", "load_minute", "load_hour", "traffic_hourly_stats")


# ── 加密辅助 ──────────────────────────────────────────────────────────────────


def _reconstruct_secrets(dest_path: str) -> None:
    """从环境变量重建 .secrets 文件（Docker 中 .secrets 归 root 所有时使用）."""
    sk = settings.SECRET_KEY
    ck = settings.CA_MASTER_KEY
    with open(dest_path, "w", encoding="utf-8") as f:
        f.write(f"COLLEI_SECRET_KEY='{sk}'\n")
        f.write(f"COLLEI_CA_MASTER_KEY='{ck}'\n")
    os.chmod(dest_path, 0o600)


def _derive_key(password: str, salt: bytes) -> bytes:
    """PBKDF2-HMAC-SHA256 从密码 + 盐值派生 32 字节 AES 密钥."""
    kdf = PBKDF2HMAC(
        algorithm=SHA256(),
        length=32,
        salt=salt,
        iterations=_PBKDF2_ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


def _encrypt(data: bytes, password: str) -> bytes:
    """加密数据，返回 magic + salt + nonce + ciphertext_with_tag."""
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    key = _derive_key(password, salt)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, data, None)
    return _MAGIC + salt + nonce + ciphertext


def _decrypt(data: bytes, password: str) -> bytes:
    """解密数据，验证 magic header，返回明文."""
    if len(data) < len(_MAGIC) + _SALT_LEN + _NONCE_LEN + 16:
        raise ValueError("文件格式不正确")
    if data[:len(_MAGIC)] != _MAGIC:
        raise ValueError("文件格式不正确：无效的魔数")
    offset = len(_MAGIC)
    salt = data[offset:offset + _SALT_LEN]
    offset += _SALT_LEN
    nonce = data[offset:offset + _NONCE_LEN]
    offset += _NONCE_LEN
    ciphertext = data[offset:]
    key = _derive_key(password, salt)
    aesgcm = AESGCM(key)
    try:
        return aesgcm.decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise ValueError("密码错误或文件已损坏") from exc


# ── SQLite 安全备份 ──────────────────────────────────────────────────────────

def _sqlite_backup(source_path: str, dest_path: str) -> None:
    """使用 sqlite3.backup() API 安全导出数据库（处理 WAL 日志一致性）."""
    src = sqlite3.connect(source_path)
    dst = sqlite3.connect(dest_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _purge_monitoring_tables(db_path: str) -> None:
    """从备份数据库中清除历史监控数据表内容."""
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        for table in _MONITORING_TABLES:
            try:
                conn.execute(f"DELETE FROM [{table}]")  # noqa: S608
            except sqlite3.OperationalError:
                pass  # 表不存在时跳过
        conn.execute("VACUUM")
    finally:
        conn.close()


# ── 备份元数据 ────────────────────────────────────────────────────────────────

def _build_meta(files: list[str], exclude_monitoring: bool) -> dict:
    from app.core.update_checker import _read_current_version
    ver = _read_current_version()
    return {
        "version": _BACKUP_VERSION,
        "created_at": int(time.time()),
        "collei_version": ver,
        "exclude_monitoring": exclude_monitoring,
        "files": files,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  端点
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/backup")
async def download_backup(
    password: str = Query(..., min_length=8, description="加密密码"),
    exclude_monitoring: bool = Query(False, description="排除历史监控数据"),
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """下载加密备份文件."""
    # 解析源数据库路径
    db_url = settings.DATABASE_URL
    if db_url.startswith("sqlite"):
        # sqlite+aiosqlite:///path 或 sqlite+aiosqlite:////abs/path
        db_path = db_url.split("///", 1)[-1]
    else:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="仅支持 SQLite 数据库备份",
        )

    if not Path(db_path).exists():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="数据库文件不存在",
        )

    tmpdir = tempfile.mkdtemp(prefix="collei-backup-")
    try:
        # 1. 安全导出数据库
        backup_db = os.path.join(tmpdir, "collei.db")
        _sqlite_backup(db_path, backup_db)

        # 清除监控数据（可选）
        if exclude_monitoring:
            _purge_monitoring_tables(backup_db)

        # 2. 收集需要备份的文件
        included: list[str] = ["collei.db"]

        for fname in (".secrets", "ssh_ca_key.enc", "ssh_ca_key.pub", "ssh_ca_key_old.pub"):
            src = _DATA_DIR / fname
            if src.exists():
                try:
                    shutil.copy2(str(src), os.path.join(tmpdir, fname))
                except PermissionError:
                    # Docker: .secrets owned by root, reconstruct from env vars
                    if fname == ".secrets":
                        _reconstruct_secrets(os.path.join(tmpdir, fname))
                    else:
                        logger.warning("无法读取 %s (权限不足), 跳过", fname)
                        continue
                included.append(fname)

        # 3. 写入元数据
        meta = _build_meta(included, exclude_monitoring)
        meta_path = os.path.join(tmpdir, "backup_meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        included.append("backup_meta.json")

        # 4. 创建 tar.gz
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
            for fname in included:
                fpath = os.path.join(tmpdir, fname)
                tar.add(fpath, arcname=f"collei-backup/{fname}")
        tar_bytes = tar_buffer.getvalue()

        # 5. 加密
        encrypted = _encrypt(tar_bytes, password)

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # 审计日志
    await audit.emit(
        db, msg_type="system", message="系统备份已下载",
        detail=f"exclude_monitoring={exclude_monitoring}, files={included}",
        user_uuid=_current_user.uuid,
    )

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    filename = f"collei-backup-{timestamp}.collei-backup"

    return StreamingResponse(
        io.BytesIO(encrypted),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/restore", response_model=RestoreResponse)
async def upload_restore(
    file: UploadFile,
    password: str = Form(..., min_length=8, description="备份加密密码"),
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """上传加密备份文件进行恢复（暂存，重启后生效）."""
    # 检查是否已有待恢复
    if _RESTORE_PENDING.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="已有一个待恢复的备份，请先取消或重启服务",
        )

    # 读取上传文件（带大小限制）
    content = await file.read()
    if len(content) > _MAX_RESTORE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件大小超过限制（最大 {_MAX_RESTORE_SIZE // 1024 // 1024} MB）",
        )

    # 解密
    try:
        tar_bytes = _decrypt(content, password)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # 解压并验证
    tmpdir = tempfile.mkdtemp(prefix="collei-restore-")
    try:
        tar_buffer = io.BytesIO(tar_bytes)
        with tarfile.open(fileobj=tar_buffer, mode="r:gz") as tar:
            # 安全检查：防止路径穿越
            for member in tar.getmembers():
                member_path = os.path.normpath(member.name)
                if member_path.startswith("..") or os.path.isabs(member_path):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="备份文件包含非法路径",
                    )
            tar.extractall(tmpdir, filter="data")

        # 查找解压后的文件（可能在 collei-backup/ 子目录下）
        extract_root = os.path.join(tmpdir, "collei-backup")
        if not os.path.isdir(extract_root):
            # 如果没有子目录，直接在 tmpdir
            extract_root = tmpdir

        # 验证必需文件
        extracted_files = set(os.listdir(extract_root))
        missing = _REQUIRED_FILES - extracted_files
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"备份文件不完整，缺少: {', '.join(sorted(missing))}",
            )

        # 读取元数据
        meta_path = os.path.join(extract_root, "backup_meta.json")
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                meta_dict = json.load(f)
        else:
            # 兼容无元数据的老备份
            meta_dict = _build_meta(sorted(extracted_files), False)

        meta = BackupMeta(**meta_dict)

        # 写入恢复暂存目录
        if _RESTORE_DIR.exists():
            shutil.rmtree(_RESTORE_DIR)
        _RESTORE_DIR.mkdir(parents=True)

        for fname in os.listdir(extract_root):
            src = os.path.join(extract_root, fname)
            dst = _RESTORE_DIR / fname
            if os.path.isfile(src):
                shutil.copy2(src, str(dst))

        # 创建待恢复标记
        _RESTORE_PENDING.write_text(
            json.dumps(meta_dict, ensure_ascii=False), encoding="utf-8",
        )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # 审计日志
    await audit.emit(
        db, msg_type="system", message="备份文件已上传，等待恢复",
        detail=f"created_at={meta.created_at}, files={meta.files}",
        user_uuid=_current_user.uuid,
    )

    return RestoreResponse(
        message="备份文件已验证并暂存，重启服务后生效",
        backup_meta=meta,
    )


@router.get("/restore/status", response_model=RestoreStatusResponse)
async def get_restore_status(
    _current_user: User = Depends(get_current_user),
):
    """查询是否有待恢复的备份."""
    if not _RESTORE_PENDING.exists():
        return RestoreStatusResponse(pending=False)

    try:
        meta_dict = json.loads(_RESTORE_PENDING.read_text(encoding="utf-8"))
        meta = BackupMeta(**meta_dict)
    except Exception:
        return RestoreStatusResponse(pending=True)

    return RestoreStatusResponse(pending=True, backup_meta=meta)


@router.delete("/restore", response_model=MessageResponse)
async def cancel_restore(
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """取消待恢复的备份."""
    if not _RESTORE_PENDING.exists() and not _RESTORE_DIR.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="没有待恢复的备份",
        )

    if _RESTORE_DIR.exists():
        shutil.rmtree(_RESTORE_DIR, ignore_errors=True)
    if _RESTORE_PENDING.exists():
        _RESTORE_PENDING.unlink(missing_ok=True)

    await audit.emit(
        db, msg_type="system", message="待恢复备份已取消",
        user_uuid=_current_user.uuid,
    )

    return MessageResponse(message="待恢复备份已取消")

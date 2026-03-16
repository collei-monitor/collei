"""SSH CA 密钥管理器 — 加密存储、TTL 缓存、密钥轮换.

文件布局（data/ 目录下）:
  ssh_ca_key.enc      — AES-256-GCM 加密的 CA 私钥
  ssh_ca_key.pub      — CA 公钥（明文，用于分发）
  ssh_ca_key_old.pub  — 轮换过渡期的旧 CA 公钥（可选）
  ssh_ca_key          — 旧版明文私钥（仅用于自动迁移，迁移后删除）
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

import asyncssh
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import BASE_DIR, settings

logger = logging.getLogger(__name__)

_DATA_DIR = BASE_DIR / "data"
_ENC_KEY_PATH = _DATA_DIR / "ssh_ca_key.enc"
_PUB_KEY_PATH = _DATA_DIR / "ssh_ca_key.pub"
_OLD_PUB_PATH = _DATA_DIR / "ssh_ca_key_old.pub"
_LEGACY_KEY_PATH = _DATA_DIR / "ssh_ca_key"  # 旧版明文路径

# 加密参数
_SALT_LEN = 16
_NONCE_LEN = 12
_HKDF_INFO = b"collei-ca-key-encryption"

# TTL 缓存
_ca_key: asyncssh.SSHKey | None = None
_ca_key_expires: float = 0.0
_ca_key_lock = asyncio.Lock()


# ── 加密辅助 ──────────────────────────────────────────────────────────────────

def _get_master_key() -> str:
    """获取 CA 主密钥（优先 CA_MASTER_KEY，降级到 SECRET_KEY）."""
    mk = settings.CA_MASTER_KEY
    if mk:
        return mk
    logger.warning(
        "COLLEI_CA_MASTER_KEY 未设置，使用 SECRET_KEY 派生 CA 加密密钥（降级模式）。"
        "建议在 .env 中设置独立的 COLLEI_CA_MASTER_KEY 以获得更高安全性。"
    )
    return settings.SECRET_KEY


def _derive_aes_key(master_key: str, salt: bytes) -> bytes:
    """从主密钥 + 盐值通过 HKDF-SHA256 派生 AES-256 密钥."""
    return HKDF(
        algorithm=SHA256(),
        length=32,
        salt=salt,
        info=_HKDF_INFO,
    ).derive(master_key.encode("utf-8"))


def _encrypt_private_key(private_key_bytes: bytes) -> bytes:
    """加密 CA 私钥，返回 salt + nonce + ciphertext_with_tag."""
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    aes_key = _derive_aes_key(_get_master_key(), salt)
    aesgcm = AESGCM(aes_key)
    ciphertext = aesgcm.encrypt(nonce, private_key_bytes, None)
    return salt + nonce + ciphertext


def _decrypt_private_key(data: bytes) -> bytes:
    """解密 CA 私钥，输入格式: salt + nonce + ciphertext_with_tag."""
    salt = data[:_SALT_LEN]
    nonce = data[_SALT_LEN : _SALT_LEN + _NONCE_LEN]
    ciphertext = data[_SALT_LEN + _NONCE_LEN :]
    aes_key = _derive_aes_key(_get_master_key(), salt)
    aesgcm = AESGCM(aes_key)
    return aesgcm.decrypt(nonce, ciphertext, None)


# ── 密钥生成与存储 ────────────────────────────────────────────────────────────

def _generate_and_save() -> asyncssh.SSHKey:
    """生成新的 Ed25519 CA 密钥对，加密存储私钥，明文存储公钥."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    key = asyncssh.generate_private_key("ssh-ed25519")
    private_bytes = key.export_private_key()
    public_str = key.export_public_key().decode()

    # 加密私钥并写入
    encrypted = _encrypt_private_key(private_bytes)
    _ENC_KEY_PATH.write_bytes(encrypted)
    try:
        _ENC_KEY_PATH.chmod(0o600)
    except OSError:
        pass

    # 公钥明文写入
    _PUB_KEY_PATH.write_text(public_str, encoding="utf-8")

    logger.info("SSH CA key generated: %s / %s", _ENC_KEY_PATH, _PUB_KEY_PATH)
    return key


def _load_from_encrypted() -> asyncssh.SSHKey:
    """从加密文件加载 CA 私钥."""
    data = _ENC_KEY_PATH.read_bytes()
    private_bytes = _decrypt_private_key(data)
    key = asyncssh.import_private_key(private_bytes.decode("utf-8"))
    logger.info("SSH CA key loaded from %s", _ENC_KEY_PATH)
    return key


def _migrate_legacy_key() -> asyncssh.SSHKey | None:
    """如果存在旧版明文私钥文件，自动迁移为加密格式."""
    if not _LEGACY_KEY_PATH.exists():
        return None

    logger.warning("发现旧版明文 CA 私钥 %s，正在迁移为加密格式...", _LEGACY_KEY_PATH)

    key = asyncssh.read_private_key(str(_LEGACY_KEY_PATH))
    private_bytes = key.export_private_key()
    public_str = key.export_public_key().decode()

    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 写入加密版本
    encrypted = _encrypt_private_key(private_bytes)
    _ENC_KEY_PATH.write_bytes(encrypted)
    try:
        _ENC_KEY_PATH.chmod(0o600)
    except OSError:
        pass

    # 写入公钥（如果还没有）
    if not _PUB_KEY_PATH.exists():
        _PUB_KEY_PATH.write_text(public_str, encoding="utf-8")

    # 删除旧明文文件
    _LEGACY_KEY_PATH.unlink()
    logger.info("CA 私钥已迁移: %s → %s (明文文件已删除)", _LEGACY_KEY_PATH, _ENC_KEY_PATH)

    return key


# ── TTL 缓存的核心 API ───────────────────────────────────────────────────────

async def get_ca_key() -> asyncssh.SSHKey:
    """获取 CA 私钥（带 TTL 缓存）.

    首次调用时：迁移旧密钥 / 从加密文件加载 / 新生成
    TTL 到期后：丢弃缓存，从磁盘重新解密
    """
    global _ca_key, _ca_key_expires

    now = time.monotonic()
    if _ca_key is not None and now < _ca_key_expires:
        return _ca_key

    async with _ca_key_lock:
        # double-check
        now = time.monotonic()
        if _ca_key is not None and now < _ca_key_expires:
            return _ca_key

        key: asyncssh.SSHKey | None = None

        # 1. 尝试加载加密文件
        if _ENC_KEY_PATH.exists():
            try:
                key = _load_from_encrypted()
            except Exception as exc:
                logger.error("CA 私钥解密失败: %s", exc)
                raise RuntimeError(
                    "CA 私钥解密失败，请检查 COLLEI_CA_MASTER_KEY 是否正确"
                ) from exc

        # 2. 尝试迁移旧版明文文件
        if key is None:
            key = _migrate_legacy_key()

        # 3. 都没有，首次生成
        if key is None:
            key = _generate_and_save()

        _ca_key = key
        _ca_key_expires = time.monotonic() + settings.CA_KEY_TTL
        return _ca_key


def get_ca_public_key() -> str:
    """获取 CA 公钥字符串（同步，从 .pub 文件读取）."""
    if _PUB_KEY_PATH.exists():
        return _PUB_KEY_PATH.read_text(encoding="utf-8").strip()

    # 如果公钥文件不存在但加密私钥存在，从私钥导出
    if _ENC_KEY_PATH.exists():
        data = _ENC_KEY_PATH.read_bytes()
        private_bytes = _decrypt_private_key(data)
        key = asyncssh.import_private_key(private_bytes.decode("utf-8"))
        pub = key.export_public_key().decode().strip()
        _PUB_KEY_PATH.write_text(pub + "\n", encoding="utf-8")
        return pub

    # 旧版明文文件
    if _LEGACY_KEY_PATH.exists():
        key = asyncssh.read_private_key(str(_LEGACY_KEY_PATH))
        return key.export_public_key().decode().strip()

    raise RuntimeError("SSH CA key not initialized")


def get_ca_public_key_for_sshd() -> str:
    """获取带 from= 限制的 CA 公钥行（用于写入 TrustedUserCAKeys 文件）."""
    raw = get_ca_public_key()
    return f'cert-authority,from="127.0.0.1,::1" {raw}'


def get_old_ca_public_key() -> str | None:
    """获取旧 CA 公钥（轮换过渡期），如果不存在返回 None."""
    if _OLD_PUB_PATH.exists():
        return _OLD_PUB_PATH.read_text(encoding="utf-8").strip()
    return None


def get_all_ca_public_keys_for_sshd() -> str:
    """获取所有有效 CA 公钥（含过渡期旧密钥），带 from= 限制，用于 TrustedUserCAKeys."""
    lines = [get_ca_public_key_for_sshd()]
    old = get_old_ca_public_key()
    if old:
        lines.append(f'cert-authority,from="127.0.0.1,::1" {old}')
    return "\n".join(lines)


# ── 密钥轮换 ──────────────────────────────────────────────────────────────────

async def rotate_ca_key() -> dict:
    """轮换 CA 密钥.

    1. 将当前公钥移为 old
    2. 生成新密钥对
    3. 清除内存缓存
    """
    global _ca_key, _ca_key_expires

    async with _ca_key_lock:
        # 将当前公钥保存为旧版
        if _PUB_KEY_PATH.exists():
            current_pub = _PUB_KEY_PATH.read_text(encoding="utf-8").strip()
            _OLD_PUB_PATH.write_text(current_pub + "\n", encoding="utf-8")
            logger.info("旧 CA 公钥已保存到 %s", _OLD_PUB_PATH)

        # 生成新密钥对
        new_key = _generate_and_save()
        _ca_key = new_key
        _ca_key_expires = time.monotonic() + settings.CA_KEY_TTL

        new_pub = new_key.export_public_key().decode().strip()
        old_pub = get_old_ca_public_key()

        return {
            "new_public_key": new_pub,
            "old_public_key": old_pub,
            "message": "CA key rotated. Old key retained for transition. "
                       "Run 'collei-agent update-ca' on all servers, "
                       "then call DELETE /ssh/ca-old-key to clean up.",
        }


async def cleanup_old_ca_key() -> bool:
    """删除旧 CA 公钥（过渡期结束后调用）."""
    if _OLD_PUB_PATH.exists():
        _OLD_PUB_PATH.unlink()
        logger.info("旧 CA 公钥已删除: %s", _OLD_PUB_PATH)
        return True
    return False

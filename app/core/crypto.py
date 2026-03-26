"""凭证加密辅助 — 基于 Fernet 对称加密."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _derive_key(secret: str) -> bytes:
    """从 SECRET_KEY 派生 32 字节密钥并 base64url 编码为 Fernet 兼容格式."""
    raw = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(raw)


def _get_fernet() -> Fernet:
    return Fernet(_derive_key(settings.SECRET_KEY))


def encrypt_credential(plaintext: str) -> str:
    """加密凭证明文，返回 base64 编码密文."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_credential(ciphertext: str) -> str:
    """解密凭证密文，返回明文字符串.

    Raises:
        InvalidToken: 密钥不匹配或数据损坏.
    """
    return _get_fernet().decrypt(ciphertext.encode()).decode()

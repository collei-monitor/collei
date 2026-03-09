"""应用全局配置 – 通过环境变量或 .env 文件加载."""

import logging
import secrets
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # 项目根目录


class Settings(BaseSettings):
    """全局配置类.
    
    加载优先级（从高到低）:
      1. 环境变量 (COLLEI_* 前缀)
      2. .env 文件
      3. 字段默认值
    """
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        env_prefix="COLLEI_",
        case_sensitive=False,
        # 环境变量优先于 .env 文件
        env_ignore_empty=False,
    )

    # ── 应用 ──────────────────────────────────────────────
    DEBUG: bool = False

    # ── 数据库 ────────────────────────────────────────────
    DATABASE_URL: str = f"sqlite+aiosqlite:///{BASE_DIR / 'collei.db'}"

    # ── JWT ───────────────────────────────────────────────
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1 天
    SESSION_EXPIRE_DAYS: int = 7

    # ── 安全 ──────────────────────────────────────────────
    LOGIN_ATTEMPT_LIMIT: int = 10  # 窗口期内最大失败次数
    LOGIN_ATTEMPT_WINDOW: int = 600  # 窗口期（秒）
    LOGIN_2FA_CHALLENGE_EXPIRE_SECONDS: int = 300  # 两阶段登录挑战有效期（秒）

    # ── 初始管理员 ────────────────────────────────────────
    DEFAULT_ADMIN_USERNAME: str = "admin"
    DEFAULT_ADMIN_PASSWORD: str = ""  # 为空时由程序随机生成并打印日志

    # ── 后台任务 ──────────────────────────────────────────
    WS_BROADCAST_INTERVAL: float = 2.0  # WebSocket 广播周期（秒）

    @model_validator(mode="after")
    def _auto_generate_secret_key(self) -> "Settings":
        """SECRET_KEY 为空时自动生成临时密钥并警告（每次重启均会失效）."""
        if not self.SECRET_KEY:
            self.SECRET_KEY = secrets.token_urlsafe(64)
            _log.warning(
                "COLLEI_SECRET_KEY 未设置，已自动生成临时密钥。"
                "重启后所有会话将失效，请在 .env 或 环境变量 中设置 COLLEI_SECRET_KEY 以持久化。"
            )
        return self


settings = Settings()

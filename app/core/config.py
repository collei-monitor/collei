"""应用全局配置 – 通过环境变量或 .env 文件加载."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    APP_NAME: str = "Collei"
    DEBUG: bool = False

    # ── 数据库 ────────────────────────────────────────────
    DATABASE_URL: str = f"sqlite+aiosqlite:///{BASE_DIR / 'collei.db'}"

    # ── JWT ───────────────────────────────────────────────
    SECRET_KEY: str = "change-me-to-a-random-secret-key"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1 天
    SESSION_EXPIRE_DAYS: int = 7

    # ── 安全 ──────────────────────────────────────────────
    LOGIN_ATTEMPT_LIMIT: int = 10  # 窗口期内最大失败次数
    LOGIN_ATTEMPT_WINDOW: int = 600  # 窗口期（秒）
    LOGIN_2FA_CHALLENGE_EXPIRE_SECONDS: int = 300  # 两阶段登录挑战有效期（秒）

    # ── 初始管理员 ────────────────────────────────────────
    DEFAULT_ADMIN_USERNAME: str = "admin"
    DEFAULT_ADMIN_PASSWORD: str = ""  # 必须通过环境变量设置

    # ── 后台任务 ──────────────────────────────────────────
    OFFLINE_THRESHOLD_SECONDS: int = 10  # 超过此时间未上报视为离线
    OFFLINE_CHECK_INTERVAL: int = 2  # 离线检测任务周期（秒）
    WS_BROADCAST_INTERVAL: float = 2.0  # WebSocket 广播周期（秒）
    LOAD_RETAIN_SECONDS: int = 80  # 实时监控数据保留时长（秒），作用于表load_now


settings = Settings()

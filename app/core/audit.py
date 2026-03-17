"""审计日志服务 — 统一的日志记录入口.

提供两种写入方式:
  1. emit()       — 在已有 db session 的上下文中同步写入
  2. background() — 火发即忘，自动创建新 session 写入（用于后台任务/无 session 场景）

用法:
  from app.core.audit import audit

  # 在 API 路由中（有 db session）
  await audit.emit(db, msg_type="auth", message="用户登录", ip=client_ip)

  # 在后台任务中（无 db session）
  await audit.background(msg_type="task", message="后台任务已启动", source="tasks")
"""

from __future__ import annotations

import logging
import time
import traceback

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("collei.audit")


class AuditLogger:
    """审计日志写入器."""

    async def emit(
        self,
        db: AsyncSession,
        *,
        level: str = "info",
        msg_type: str,
        message: str,
        detail: str | None = None,
        source: str | None = None,
        ip: str | None = None,
        user_uuid: str | None = None,
        server_uuid: str | None = None,
    ) -> None:
        """在现有 session 中写入审计日志（调用方负责 commit）."""
        from app.crud.notification import create_log

        await create_log(
            db,
            level=level,
            msg_type=msg_type,
            message=message,
            detail=detail,
            source=source,
            ip=ip,
            user_uuid=user_uuid,
            server_uuid=server_uuid,
        )
        # 同时写入 Python logging
        _log(level, msg_type, message, detail)

    async def background(
        self,
        *,
        level: str = "info",
        msg_type: str,
        message: str,
        detail: str | None = None,
        source: str | None = None,
        ip: str | None = None,
        user_uuid: str | None = None,
        server_uuid: str | None = None,
    ) -> None:
        """自管理 session 写入审计日志（火发即忘，内部 commit）."""
        from app.crud.notification import create_log
        from app.db.session import async_session_factory

        try:
            async with async_session_factory() as session:
                await create_log(
                    session,
                    level=level,
                    msg_type=msg_type,
                    message=message,
                    detail=detail,
                    source=source,
                    ip=ip,
                    user_uuid=user_uuid,
                    server_uuid=server_uuid,
                )
                await session.commit()
        except Exception:
            logger.error("审计日志写入失败: %s", traceback.format_exc())

        _log(level, msg_type, message, detail)

    async def error(
        self,
        *,
        msg_type: str,
        message: str,
        exc: Exception | None = None,
        source: str | None = None,
        server_uuid: str | None = None,
    ) -> None:
        """记录错误级别审计日志的便捷方法."""
        detail = traceback.format_exc() if exc else None
        await self.background(
            level="error",
            msg_type=msg_type,
            message=message,
            detail=detail,
            source=source,
            server_uuid=server_uuid,
        )


def _log(level: str, msg_type: str, message: str, detail: str | None) -> None:
    """同步写入 Python logging."""
    text = f"[{msg_type}] {message}"
    if detail:
        text += f" | {detail}"
    if level == "error":
        logger.error(text)
    elif level == "warning":
        logger.warning(text)
    else:
        logger.info(text)


# 全局单例
audit = AuditLogger()

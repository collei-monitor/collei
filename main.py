"""Collei — FastAPI 应用入口."""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_v1_router
from app.core.config import settings
from app.core.security import hash_password
from app.db.session import async_session_factory, engine

# 确保所有模型被导入以便 metadata 完整
import app.db.base  # noqa: F401

FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    """应用生命周期 — 启动时确保默认管理员存在, 启动后台任务."""
    from app.core.tasks import background_tasks

    await _ensure_default_admin()
    await _ensure_default_configs(application)
    await background_tasks.start()
    yield
    # shutdown
    await background_tasks.stop()
    await engine.dispose()


async def _ensure_default_admin() -> None:
    """如果 users 表为空，创建默认管理员账号（密码未配置时随机生成并打印日志）."""
    from app.crud.auth import create_user, get_user_by_username

    async with async_session_factory() as session:
        existing = await get_user_by_username(session, settings.DEFAULT_ADMIN_USERNAME)
        if existing is None:
            password = settings.DEFAULT_ADMIN_PASSWORD
            if not password:
                password = secrets.token_urlsafe(12)
                logger.warning(
                    "COLLEI_DEFAULT_ADMIN_PASSWORD 未设置，已为用户 '%s' 生成随机密码: %s",
                    settings.DEFAULT_ADMIN_USERNAME,
                    password,
                )
            await create_user(
                session,
                username=settings.DEFAULT_ADMIN_USERNAME,
                passwd_hash=hash_password(password),
            )
            await session.commit()


# 各配置项的默认值（仅在数据库中不存在时写入）
_DEFAULT_CONFIGS: dict[str, str] = {
    "ip_db": "GeoLite2",
    "app_name": "Collei",
    "offline_threshold_seconds": "10",
    "offline_check_interval": "2",
    "load_retain_seconds": "80",
}


async def _ensure_default_configs(application: "FastAPI") -> None:
    """初始化 config 表的默认配置项，并确保 global_registration_token 存在."""
    from app.crud import config as crud_config

    async with async_session_factory() as session:
        # 确保 global_registration_token 存在
        token = await crud_config.get_config_value(session, "global_registration_token")
        if not token:
            new_token = secrets.token_urlsafe(32)
            await crud_config.set_config(session, "global_registration_token", new_token)
            logger.info("global_registration_token 未配置，已自动生成: %s", new_token)

        # 初始化其余默认配置（仅在数据库中不存在时写入）
        for key, default_value in _DEFAULT_CONFIGS.items():
            existing = await crud_config.get_config_value(session, key)
            if existing is None:
                await crud_config.set_config(session, key, default_value)

        # 从数据库读取 app_name 并更新 FastAPI 应用标题
        app_name = await crud_config.get_config_value(session, "app_name")
        if app_name:
            application.title = app_name

        # 预加载配置缓存（覆盖本次刚写入的所有初始值）
        from app.core.config_cache import config_cache
        await config_cache.preload(session)

        # 预加载服务器数据缓存（离线检测与广播快照均从内存读取）
        from app.core.server_cache import server_cache
        await server_cache.preload(session)


def create_app() -> FastAPI:
    application = FastAPI(
        title="Collei",
        version="0.1.0",
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        lifespan=lifespan,
    )
    application.include_router(api_v1_router)

    # 托管前端静态资源（SPA）——仅当前端构建产物存在时挂载
    if FRONTEND_DIST.exists():
        spa_index = FRONTEND_DIST / "index.html"
        application.mount(
            "/",
            StaticFiles(directory=str(FRONTEND_DIST), html=True),
            name="spa",
        )

        # SPA 路由回退：非 API 路径的 404 返回 index.html，由前端路由接管
        @application.exception_handler(404)
        async def _spa_fallback(request: Request, exc: HTTPException):
            if not request.url.path.startswith("/api/") and spa_index.exists():
                return HTMLResponse(spa_index.read_text(encoding="utf-8"))
            return HTMLResponse(
                content='{"detail":"Not Found"}',
                status_code=404,
                media_type="application/json",
            )

    return application


app = create_app()

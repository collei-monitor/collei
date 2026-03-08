"""Collei — FastAPI 应用入口."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_v1_router
from app.core.config import settings
from app.core.security import hash_password
from app.db.session import async_session_factory, engine

# 确保所有模型被导入以便 metadata 完整
import app.db.base  # noqa: F401

FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(application: FastAPI):
    """应用生命周期 — 启动时确保默认管理员存在, 启动后台任务."""
    from app.core.tasks import background_tasks

    await _ensure_default_admin()
    await background_tasks.start()
    yield
    # shutdown
    await background_tasks.stop()
    await engine.dispose()


async def _ensure_default_admin() -> None:
    """如果 users 表为空，创建默认管理员账号."""
    from app.crud.auth import create_user, get_user_by_username

    if not settings.DEFAULT_ADMIN_PASSWORD:
        return  # 未配置密码则跳过

    async with async_session_factory() as session:
        existing = await get_user_by_username(session, settings.DEFAULT_ADMIN_USERNAME)
        if existing is None:
            await create_user(
                session,
                username=settings.DEFAULT_ADMIN_USERNAME,
                passwd_hash=hash_password(settings.DEFAULT_ADMIN_PASSWORD),
            )
            await session.commit()


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        lifespan=lifespan,
    )
    application.include_router(api_v1_router)

    # 托管前端静态资源（SPA）——仅当前端构建产物存在时挂载
    if FRONTEND_DIST.exists():
        application.mount(
            "/",
            StaticFiles(directory=str(FRONTEND_DIST), html=True),
            name="spa",
        )

    return application


app = create_app()

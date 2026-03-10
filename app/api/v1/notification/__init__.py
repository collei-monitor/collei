"""告警与通知管理 API 路由包."""

from fastapi import APIRouter

from app.api.v1.notification.channels import router as channels_router
from app.api.v1.notification.engine import router as engine_router
from app.api.v1.notification.history import router as history_router
from app.api.v1.notification.providers import router as providers_router
from app.api.v1.notification.rules import router as rules_router

router = APIRouter(prefix="/notifications", tags=["notifications"])

router.include_router(providers_router)
router.include_router(channels_router)
router.include_router(rules_router)
router.include_router(history_router)
router.include_router(engine_router)

"""客户端与节点管理 API 路由包."""

from fastapi import APIRouter

from app.api.v1.clients.billing import router as billing_router
from app.api.v1.clients.groups import router as groups_router
from app.api.v1.clients.monitoring import router as monitoring_router
from app.api.v1.clients.network import router as network_router
from app.api.v1.clients.public import router as public_router
from app.api.v1.clients.servers import router as servers_router

router = APIRouter(prefix="/clients", tags=["clients"])

router.include_router(public_router)
router.include_router(servers_router)
router.include_router(groups_router)
router.include_router(monitoring_router)
router.include_router(billing_router)
router.include_router(network_router)

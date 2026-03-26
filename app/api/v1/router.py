"""v1 API 路由聚合."""

from fastapi import APIRouter

from app.api.v1.agent import router as agent_router
from app.api.v1.auth import router as auth_router
from app.api.v1.clients import router as clients_router
from app.api.v1.config import router as config_router
from app.api.v1.logs import router as logs_router
from app.api.v1.dns import router as dns_router
from app.api.v1.notification import router as notification_router
from app.api.v1.public import router as public_router
from app.api.v1.sso import router as sso_router
from app.api.v1.themes import router as themes_router
from app.api.v1.ws import router as ws_router
from app.api.v1.ws_sftp import router as ws_sftp_router
from app.api.v1.ws_ssh import router as ws_ssh_router

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(auth_router)
api_v1_router.include_router(sso_router)
api_v1_router.include_router(public_router)
api_v1_router.include_router(clients_router)
api_v1_router.include_router(agent_router)
api_v1_router.include_router(themes_router)
api_v1_router.include_router(config_router)
api_v1_router.include_router(ws_router)
api_v1_router.include_router(ws_ssh_router)
api_v1_router.include_router(ws_sftp_router)
api_v1_router.include_router(notification_router)
api_v1_router.include_router(dns_router)
api_v1_router.include_router(logs_router)


"""v1 API 路由聚合."""

from fastapi import APIRouter

from app.api.v1.agent import router as agent_router
from app.api.v1.auth import router as auth_router
from app.api.v1.clients import router as clients_router
from app.api.v1.config import router as config_router
from app.api.v1.ws import router as ws_router

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(auth_router)
api_v1_router.include_router(clients_router)
api_v1_router.include_router(agent_router)
api_v1_router.include_router(config_router)
api_v1_router.include_router(ws_router)

"""健康检查端点 — 用于容器健康检测和负载均衡探针.

端点:
  GET  /health   返回服务状态（无需认证）
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """服务健康检查 — 无需认证."""
    return HealthResponse(status="ok")

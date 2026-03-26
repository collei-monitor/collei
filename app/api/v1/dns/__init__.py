"""DNS 管理 API 路由包."""

from fastapi import APIRouter

from app.api.v1.dns.credentials import router as credentials_router
from app.api.v1.dns.domains import router as domains_router
from app.api.v1.dns.records import router as records_router
from app.api.v1.dns.ddns import router as ddns_router

router = APIRouter(prefix="/dns", tags=["dns"])

router.include_router(credentials_router)
router.include_router(domains_router)
router.include_router(records_router)
router.include_router(ddns_router)

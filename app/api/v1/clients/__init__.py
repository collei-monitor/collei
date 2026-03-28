"""客户端与节点管理 API 路由包."""

from fastapi import APIRouter

from app.api.v1.clients.billing import router as billing_router
from app.api.v1.clients.groups import router as groups_router
from app.api.v1.clients.monitoring import router as monitoring_router
from app.api.v1.clients.network import router as network_router
from app.api.v1.clients.public import router as public_router
from app.api.v1.clients.servers import router as servers_router
from app.api.v1.clients.sftp import router as sftp_router
from app.api.v1.clients.ssh import router as ssh_router
from app.api.v1.clients.tasks import router as tasks_router
from app.api.v1.clients.traffic import router as traffic_router
from app.api.v1.clients.ssh_scripts import router as ssh_scripts_router
from app.api.v1.clients.terminal import router as terminal_router
from app.api.v1.clients.files import router as files_router

router = APIRouter(prefix="/clients", tags=["clients"])

router.include_router(public_router)
router.include_router(servers_router)
router.include_router(groups_router)
router.include_router(monitoring_router)
router.include_router(billing_router)
router.include_router(traffic_router)
router.include_router(network_router)
router.include_router(ssh_router)
router.include_router(sftp_router)
router.include_router(tasks_router)
router.include_router(ssh_scripts_router)
router.include_router(terminal_router)
router.include_router(files_router)

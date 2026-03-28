"""Web File API 会话管理 API 路由（需管理员登录）.

端点:
  POST    /clients/servers/{uuid}/files/sessions              创建文件 API 会话
  GET     /clients/servers/{uuid}/files/sessions              查询活跃文件会话
  DELETE  /clients/servers/{uuid}/files/sessions/{session_id} 终止文件会话
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_client_ip, get_current_user
from app.core.fileapi_manager import fileapi_manager
from app.core.server_cache import server_cache
from app.crud import clients as crud_clients
from app.db.session import get_async_session
from app.models.auth import User

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class FileSessionCreateResponse(BaseModel):
    session_id: str
    ws_url: str


class FileSessionInfo(BaseModel):
    session_id: str
    connected_at: int
    client_ip: str


class FileSessionListResponse(BaseModel):
    sessions: list[FileSessionInfo]


# ── 路由 ──────────────────────────────────────────────────────────────────────

@router.post(
    "/servers/{uuid}/files/sessions",
    response_model=FileSessionCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_file_session(
    uuid: str,
    request: Request,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """创建文件 API 会话."""
    server = await crud_clients.get_server_by_uuid(db, uuid)
    if not server or server.is_approved != 1:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server not found",
        )

    cached_status = server_cache._statuses.get(uuid)
    if not cached_status or cached_status.get("status") != 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent not connected",
        )

    client_ip = get_client_ip(request)

    try:
        session = await fileapi_manager.create_session(
            server_uuid=uuid,
            client_ip=client_ip,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    ws_url = f"/api/v1/ws/files?session_id={session.session_id}"

    return FileSessionCreateResponse(
        session_id=session.session_id,
        ws_url=ws_url,
    )


@router.get(
    "/servers/{uuid}/files/sessions",
    response_model=FileSessionListResponse,
)
async def list_file_sessions(
    uuid: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """查询指定服务器的活跃文件 API 会话."""
    server = await crud_clients.get_server_by_uuid(db, uuid)
    if not server or server.is_approved != 1:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server not found",
        )

    sessions = fileapi_manager.get_server_sessions(uuid)
    return FileSessionListResponse(
        sessions=[
            FileSessionInfo(
                session_id=s.session_id,
                connected_at=s.created_at,
                client_ip=s.client_ip,
            )
            for s in sessions
        ],
    )


@router.delete(
    "/servers/{uuid}/files/sessions/{session_id}",
)
async def terminate_file_session(
    uuid: str,
    session_id: str,
    _current_user: User = Depends(get_current_user),
):
    """终止指定的文件 API 会话."""
    session = fileapi_manager.get_session(session_id)
    if not session or session.server_uuid != uuid:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    await fileapi_manager.remove_session(session_id)
    return {"message": "Session terminated"}

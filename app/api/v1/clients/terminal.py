"""Web Terminal 会话管理 API 路由（需管理员登录）.

端点:
  POST    /clients/servers/{uuid}/terminal/sessions              创建终端会话
  GET     /clients/servers/{uuid}/terminal/sessions              查询活跃终端会话
  DELETE  /clients/servers/{uuid}/terminal/sessions/{session_id} 终止终端会话
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_client_ip, get_current_user
from app.core.server_cache import server_cache
from app.core.terminal_manager import terminal_manager
from app.crud import clients as crud_clients
from app.db.session import get_async_session
from app.models.auth import User

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class TerminalSessionCreateRequest(BaseModel):
    cols: int = Field(80, ge=1, le=500, description="初始终端列数")
    rows: int = Field(24, ge=1, le=200, description="初始终端行数")
    shell: str = Field("", max_length=256, description="指定 shell（空字符串使用默认）")


class TerminalSessionCreateResponse(BaseModel):
    session_id: str
    ws_url: str


class TerminalSessionInfo(BaseModel):
    session_id: str
    connected_at: int
    client_ip: str


class TerminalSessionListResponse(BaseModel):
    sessions: list[TerminalSessionInfo]


# ── 路由 ──────────────────────────────────────────────────────────────────────

@router.post(
    "/servers/{uuid}/terminal/sessions",
    response_model=TerminalSessionCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_terminal_session(
    uuid: str,
    body: TerminalSessionCreateRequest,
    request: Request,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """创建终端会话（ConPTY 直连模式）."""
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
        session = await terminal_manager.create_session(
            server_uuid=uuid,
            cols=body.cols,
            rows=body.rows,
            shell=body.shell,
            client_ip=client_ip,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    ws_url = f"/api/v1/ws/terminal?session_id={session.session_id}"

    return TerminalSessionCreateResponse(
        session_id=session.session_id,
        ws_url=ws_url,
    )


@router.get(
    "/servers/{uuid}/terminal/sessions",
    response_model=TerminalSessionListResponse,
)
async def list_terminal_sessions(
    uuid: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """查询指定服务器的活跃终端会话."""
    server = await crud_clients.get_server_by_uuid(db, uuid)
    if not server or server.is_approved != 1:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server not found",
        )

    sessions = terminal_manager.get_server_sessions(uuid)
    return TerminalSessionListResponse(
        sessions=[
            TerminalSessionInfo(
                session_id=s.session_id,
                connected_at=s.created_at,
                client_ip=s.client_ip,
            )
            for s in sessions
        ],
    )


@router.delete(
    "/servers/{uuid}/terminal/sessions/{session_id}",
)
async def terminate_terminal_session(
    uuid: str,
    session_id: str,
    _current_user: User = Depends(get_current_user),
):
    """终止指定的终端会话."""
    session = terminal_manager.get_session(session_id)
    if not session or session.server_uuid != uuid:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    await terminal_manager.remove_session(session_id)
    return {"message": "Session terminated"}

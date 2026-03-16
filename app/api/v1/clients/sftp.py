"""Web SFTP 会话管理 API 路由（需管理员登录）.

端点:
  POST    /clients/servers/{uuid}/sftp/sessions             创建 SFTP 会话
  GET     /clients/servers/{uuid}/sftp/sessions             查询活跃 SFTP 会话
  DELETE  /clients/servers/{uuid}/sftp/sessions/{session_id} 终止 SFTP 会话
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_client_ip, get_current_user
from app.core.server_cache import server_cache
from app.core.sftp_manager import sftp_manager
from app.crud import clients as crud_clients
from app.db.session import get_async_session
from app.models.auth import User
from app.schemas.sftp import (
    SFTPSessionCreateRequest,
    SFTPSessionCreateResponse,
    SFTPSessionInfo,
    SFTPSessionListResponse,
)

router = APIRouter()


@router.post(
    "/servers/{uuid}/sftp/sessions",
    response_model=SFTPSessionCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_sftp_session(
    uuid: str,
    body: SFTPSessionCreateRequest,
    request: Request,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """创建 SFTP 会话.

    1. 验证服务器存在且已批准
    2. 验证 Agent 在线
    3. 创建 pending 会话，标记 server 需要隧道
    4. 返回 session_id + ws_url
    """
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
        session = await sftp_manager.create_session(
            server_uuid=uuid,
            username=body.username,
            password=body.password,
            client_ip=client_ip,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    ws_url = f"/api/v1/ws/sftp?session_id={session.session_id}"

    return SFTPSessionCreateResponse(
        session_id=session.session_id,
        ws_url=ws_url,
    )


@router.get(
    "/servers/{uuid}/sftp/sessions",
    response_model=SFTPSessionListResponse,
)
async def list_sftp_sessions(
    uuid: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """查询指定服务器的活跃 SFTP 会话."""
    server = await crud_clients.get_server_by_uuid(db, uuid)
    if not server or server.is_approved != 1:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server not found",
        )

    sessions = sftp_manager.get_server_sessions(uuid)
    return SFTPSessionListResponse(
        sessions=[
            SFTPSessionInfo(
                session_id=s.session_id,
                username=s.username,
                connected_at=s.created_at,
                client_ip=s.client_ip,
            )
            for s in sessions
        ],
    )


@router.delete(
    "/servers/{uuid}/sftp/sessions/{session_id}",
)
async def terminate_sftp_session(
    uuid: str,
    session_id: str,
    _current_user: User = Depends(get_current_user),
):
    """终止指定的 SFTP 会话."""
    session = sftp_manager.get_session(session_id)
    if not session or session.server_uuid != uuid:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    await sftp_manager.remove_session(session_id)
    return {"message": "Session closed"}

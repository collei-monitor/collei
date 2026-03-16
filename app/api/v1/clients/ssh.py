"""Web SSH 会话管理 API 路由（需管理员登录）.

端点:
  POST    /clients/servers/{uuid}/ssh/sessions             创建 SSH 会话
  GET     /clients/servers/{uuid}/ssh/sessions             查询活跃 SSH 会话
  DELETE  /clients/servers/{uuid}/ssh/sessions/{session_id} 终止 SSH 会话
  GET     /clients/ssh/ca-public-key                       获取 SSH CA 公钥
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_client_ip, get_current_user
from app.core.server_cache import server_cache
from app.core.ssh_manager import ssh_manager
from app.crud import clients as crud_clients
from app.db.session import get_async_session
from app.models.auth import User
from app.schemas.ssh import (
    SSHSessionCreateRequest,
    SSHSessionCreateResponse,
    SSHSessionInfo,
    SSHSessionListResponse,
)

router = APIRouter()


@router.post(
    "/servers/{uuid}/ssh/sessions",
    response_model=SSHSessionCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_ssh_session(
    uuid: str,
    body: SSHSessionCreateRequest,
    request: Request,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """创建 SSH 会话.

    1. 验证服务器存在且已批准
    2. 验证 Agent 在线（缓存中有该 server）
    3. 创建 pending 会话，标记 server 需要 SSH 隧道
    4. 返回 session_id + ws_url（前端连接 WebSocket）
    """
    # 验证服务器存在
    server = await crud_clients.get_server_by_uuid(db, uuid)
    if not server or server.is_approved != 1:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server not found",
        )

    # 验证 Agent 是否在线（通过缓存状态判断）
    cached_status = server_cache._statuses.get(uuid)
    if not cached_status or cached_status.get("status") != 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent not connected",
        )

    client_ip = get_client_ip(request)

    try:
        session = await ssh_manager.create_session(
            server_uuid=uuid,
            username=body.username,
            cols=body.cols,
            rows=body.rows,
            client_ip=client_ip,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    ws_url = f"/api/v1/ws/ssh?session_id={session.session_id}"

    return SSHSessionCreateResponse(
        session_id=session.session_id,
        ws_url=ws_url,
    )


@router.get(
    "/servers/{uuid}/ssh/sessions",
    response_model=SSHSessionListResponse,
)
async def list_ssh_sessions(
    uuid: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """查询指定服务器的活跃 SSH 会话."""
    server = await crud_clients.get_server_by_uuid(db, uuid)
    if not server or server.is_approved != 1:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server not found",
        )

    sessions = ssh_manager.get_server_sessions(uuid)
    return SSHSessionListResponse(
        sessions=[
            SSHSessionInfo(
                session_id=s.session_id,
                username=s.username,
                connected_at=s.created_at,
                client_ip=s.client_ip,
            )
            for s in sessions
        ],
    )


@router.delete(
    "/servers/{uuid}/ssh/sessions/{session_id}",
)
async def terminate_ssh_session(
    uuid: str,
    session_id: str,
    _current_user: User = Depends(get_current_user),
):
    """终止指定的 SSH 会话."""
    session = ssh_manager.get_session(session_id)
    if not session or session.server_uuid != uuid:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    await ssh_manager.remove_session(session_id)
    return {"message": "Session terminated"}


@router.get("/ssh/ca-public-key")
async def get_ssh_ca_public_key():
    """获取 SSH CA 公钥.

    返回当前有效的 CA 公钥（及轮换过渡期的旧公钥）。
    from= 等 sshd 限制由 Agent 安装脚本负责拼装。
    公钥本身是公开信息，无需鉴权，便于安装脚本和 Agent 直接获取。
    """
    from app.core.ca_manager import get_ca_key, get_ca_public_key, get_old_ca_public_key

    try:
        await get_ca_key()  # 确保密钥已初始化（首次部署时自动生成）
        public_key = get_ca_public_key()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SSH CA not available",
        )
    result: dict = {"public_key": public_key}
    old = get_old_ca_public_key()
    if old:
        result["old_public_key"] = old
    return result


@router.post("/ssh/ca-rotate")
async def rotate_ssh_ca_key(
    _current_user: User = Depends(get_current_user),
):
    """轮换 SSH CA 密钥.

    生成新的 CA 密钥对，旧公钥保留用于过渡。
    所有 Agent 需执行 update-ca 更新 sshd 配置。
    """
    from app.core.ca_manager import rotate_ca_key

    result = await rotate_ca_key()
    return result


@router.delete("/ssh/ca-old-key")
async def cleanup_old_ssh_ca_key(
    _current_user: User = Depends(get_current_user),
):
    """清理旧 CA 公钥（过渡期结束后调用）."""
    from app.core.ca_manager import cleanup_old_ca_key

    removed = await cleanup_old_ca_key()
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No old CA key to clean up",
        )
    return {"message": "Old CA key removed"}

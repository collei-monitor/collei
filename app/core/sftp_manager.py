"""SFTP 会话管理器 — Web SFTP 功能的核心调度组件.

职责:
  - 维护 session_id → SFTPSession 映射
  - 与 ssh_manager 共享 Agent 隧道 WS 和并发上限
  - 管理 SFTP 会话生命周期（创建 / 查询 / 删除）
  - WS 断开时自动清理 SSH 连接和 SFTP 子系统
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid as _uuid
from dataclasses import dataclass, field

import typing

import asyncssh
from fastapi import WebSocket

from app.core.ssh_manager import (
    MAX_SESSIONS_PER_SERVER,
    SESSION_IDLE_TIMEOUT,
    SESSION_MAX_DURATION,
    ssh_manager,
)

logger = logging.getLogger(__name__)


@dataclass
class SFTPSession:
    """一个 SFTP 会话的状态."""

    session_id: str
    server_uuid: str
    username: str
    password: str | None = None       # POST 创建时传入，认证后清除
    home_dir: str = ""
    created_at: int = field(default_factory=lambda: int(time.time()))
    client_ip: str = "unknown"
    last_activity: int = field(default_factory=lambda: int(time.time()))

    # 运行时持有
    user_ws: WebSocket | None = None
    conn: asyncssh.SSHClientConnection | None = field(default=None, repr=False)
    sftp: asyncssh.SFTPClient | None = field(default=None, repr=False)
    bridge_task: asyncio.Task | None = field(default=None, repr=False)
    bridge_writer: asyncio.StreamWriter | None = field(default=None, repr=False)

    # 生命周期事件（与 SSH 终端完全一致）
    tunnel_ready: asyncio.Event = field(default_factory=asyncio.Event)
    tunnel_confirm: asyncio.Event = field(default_factory=asyncio.Event)
    closed: asyncio.Event = field(default_factory=asyncio.Event)

    # 上传状态跟踪
    _upload_remaining: int = 0
    _upload_request_id: str = ""
    _upload_file: typing.Any = field(default=None, repr=False)
    _upload_received: int = 0
    _upload_path: str = ""
    _upload_last_progress: int = 0

    def touch(self) -> None:
        self.last_activity = int(time.time())

    @property
    def is_expired(self) -> bool:
        now = int(time.time())
        if now - self.created_at > SESSION_MAX_DURATION:
            return True
        if now - self.last_activity > SESSION_IDLE_TIMEOUT:
            return True
        return False

    def clear_password(self) -> None:
        """认证成功后清除内存中的密码."""
        self.password = None


class SFTPSessionManager:
    """全局 SFTP 会话管理器（单例）.

    与 ssh_manager 共享:
      - Agent 隧道 WS（_agent_tunnels）
      - tunnel_needed 标记
      - 并发上限（MAX_SESSIONS_PER_SERVER 在两者间合计）
    """

    def __init__(self) -> None:
        self._sessions: dict[str, SFTPSession] = {}
        self._server_sessions: dict[str, set[str]] = {}
        self._lock = asyncio.Lock()

    async def create_session(
        self,
        *,
        server_uuid: str,
        username: str,
        password: str | None = None,
        client_ip: str = "unknown",
    ) -> SFTPSession:
        """创建新的 SFTP 会话并标记该 server 需要隧道."""
        async with self._lock:
            self._cleanup_stale_sessions_locked(server_uuid)

            # 与 SSH 终端共享并发上限
            ssh_count = len(ssh_manager._server_sessions.get(server_uuid, set()))
            sftp_count = len(self._server_sessions.get(server_uuid, set()))
            if ssh_count + sftp_count >= MAX_SESSIONS_PER_SERVER:
                raise ValueError(
                    f"Server {server_uuid} has reached max concurrent sessions"
                )

            session_id = str(_uuid.uuid4())
            session = SFTPSession(
                session_id=session_id,
                server_uuid=server_uuid,
                username=username,
                password=password,
                client_ip=client_ip,
            )
            self._sessions[session_id] = session
            self._server_sessions.setdefault(server_uuid, set()).add(session_id)

            # 标记 server 需要隧道（共享 ssh_manager 的标记）
            ssh_manager._tunnel_needed[server_uuid] = True

            if ssh_manager.has_agent_tunnel(server_uuid):
                session.tunnel_ready.set()
                logger.info(
                    "SFTP session %s: Agent tunnel already connected", session_id
                )
            else:
                logger.info(
                    "SFTP session %s: waiting for Agent tunnel (server=%s)",
                    session_id, server_uuid,
                )

            return session

    def _cleanup_stale_sessions_locked(self, server_uuid: str) -> None:
        """清理过期或未连接的 SFTP 会话."""
        connect_timeout = 60
        now = int(time.time())
        ids = list(self._server_sessions.get(server_uuid, set()))
        for sid in ids:
            session = self._sessions.get(sid)
            if session is None:
                self._server_sessions.get(server_uuid, set()).discard(sid)
                continue
            if session.is_expired or session.closed.is_set():
                self._sessions.pop(sid, None)
                self._server_sessions.get(server_uuid, set()).discard(sid)
                continue
            if session.user_ws is None and (now - session.created_at) > connect_timeout:
                session.closed.set()
                self._sessions.pop(sid, None)
                self._server_sessions.get(server_uuid, set()).discard(sid)

    def get_session(self, session_id: str) -> SFTPSession | None:
        return self._sessions.get(session_id)

    def get_server_sessions(self, server_uuid: str) -> list[SFTPSession]:
        ids = self._server_sessions.get(server_uuid, set())
        return [self._sessions[sid] for sid in ids if sid in self._sessions]

    async def remove_session(self, session_id: str) -> None:
        """移除 SFTP 会话并清理资源."""
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is None:
                return
            session.closed.set()
            server_uuid = session.server_uuid
            ids = self._server_sessions.get(server_uuid, set())
            ids.discard(session_id)

            # 关闭 SFTP 和 SSH 连接
            if session.sftp:
                try:
                    session.sftp.exit()
                except Exception:
                    pass
                session.sftp = None
            if session.conn:
                try:
                    session.conn.close()
                except Exception:
                    pass
                session.conn = None
            if session.bridge_task and not session.bridge_task.done():
                session.bridge_task.cancel()
            if session.bridge_writer:
                try:
                    session.bridge_writer.close()
                except Exception:
                    pass
                session.bridge_writer = None

            if not ids:
                self._server_sessions.pop(server_uuid, None)

            # 检查 SSH + SFTP 所有会话是否都结束
            ssh_remaining = ssh_manager._server_sessions.get(server_uuid, set())
            sftp_remaining = self._server_sessions.get(server_uuid, set())
            if not ssh_remaining and not sftp_remaining:
                ssh_manager._tunnel_needed[server_uuid] = False
                agent_ws = ssh_manager.get_agent_tunnel(server_uuid)
                if agent_ws:
                    try:
                        await agent_ws.send_json({"type": "disconnect"})
                    except Exception:
                        pass
                logger.info(
                    "Server %s: 所有会话已结束 (SSH+SFTP), 标记 tunnel_needed=false",
                    server_uuid,
                )

    async def cleanup_expired(self) -> None:
        """清理过期 SFTP 会话."""
        expired = [sid for sid, s in self._sessions.items() if s.is_expired]
        for sid in expired:
            logger.info("Cleaning up expired SFTP session: %s", sid)
            await self.remove_session(sid)


# 全局单例
sftp_manager = SFTPSessionManager()

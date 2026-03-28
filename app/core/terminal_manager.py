"""终端会话管理器 — Web Terminal (ConPTY) 直连模式的核心调度组件.

与 SSH 隧道模式不同，终端直连模式下 Backend 作为纯 WS 中继：
  Frontend ↔ Backend (WS relay) ↔ Agent (ConPTY)

职责:
  - 维护 session_id → TerminalSession 映射
  - 维护 server_uuid → Agent 终端 WS 映射
  - 维护 server_uuid → terminal_needed 标记（report 响应下发依据）
  - 前端 WS 断开时自动清理终端会话
  - Agent WS 断开时终止该 Agent 上所有活跃终端会话
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid as _uuid
from dataclasses import dataclass, field

from fastapi import WebSocket

logger = logging.getLogger(__name__)

MAX_TERMINAL_SESSIONS_PER_SERVER = 5
SESSION_IDLE_TIMEOUT = 600
SESSION_MAX_DURATION = 7200


@dataclass
class TerminalSession:
    """一个终端直连会话的状态."""

    session_id: str
    server_uuid: str
    cols: int = 80
    rows: int = 24
    shell: str = ""
    created_at: int = field(default_factory=lambda: int(time.time()))
    client_ip: str = "unknown"

    # 运行时
    user_ws: WebSocket | None = None
    last_activity: int = field(default_factory=lambda: int(time.time()))

    # 生命周期事件
    terminal_ready: asyncio.Event = field(default_factory=asyncio.Event)
    closed: asyncio.Event = field(default_factory=asyncio.Event)

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


class TerminalSessionManager:
    """全局终端会话管理器（单例）."""

    def __init__(self) -> None:
        self._sessions: dict[str, TerminalSession] = {}
        self._agent_tunnels: dict[str, WebSocket] = {}
        self._tunnel_needed: dict[str, bool] = {}
        self._server_sessions: dict[str, set[str]] = {}
        self._lock = asyncio.Lock()

    async def create_session(
        self,
        *,
        server_uuid: str,
        cols: int = 80,
        rows: int = 24,
        shell: str = "",
        client_ip: str = "unknown",
    ) -> TerminalSession:
        """创建新的终端会话并标记该 server 需要终端 WS."""
        async with self._lock:
            self._cleanup_stale_sessions_locked(server_uuid)

            existing = self._server_sessions.get(server_uuid, set())
            if len(existing) >= MAX_TERMINAL_SESSIONS_PER_SERVER:
                raise ValueError(
                    f"Server {server_uuid} has reached max concurrent terminal sessions"
                )

            session_id = str(_uuid.uuid4())
            session = TerminalSession(
                session_id=session_id,
                server_uuid=server_uuid,
                cols=cols,
                rows=rows,
                shell=shell,
                client_ip=client_ip,
            )
            self._sessions[session_id] = session
            self._server_sessions.setdefault(server_uuid, set()).add(session_id)
            self._tunnel_needed[server_uuid] = True

            if server_uuid in self._agent_tunnels:
                session.terminal_ready.set()
                logger.info(
                    "Terminal session %s: Agent WS already connected", session_id
                )
            else:
                logger.info(
                    "Terminal session %s: waiting for Agent WS (server=%s)",
                    session_id, server_uuid,
                )

            return session

    def _cleanup_stale_sessions_locked(self, server_uuid: str) -> None:
        """清理指定 server 上过期或未连接的会话."""
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

        remaining = self._server_sessions.get(server_uuid, set())
        if not remaining:
            self._server_sessions.pop(server_uuid, None)
            self._tunnel_needed[server_uuid] = False

    def get_session(self, session_id: str) -> TerminalSession | None:
        return self._sessions.get(session_id)

    def get_server_sessions(self, server_uuid: str) -> list[TerminalSession]:
        ids = self._server_sessions.get(server_uuid, set())
        return [self._sessions[sid] for sid in ids if sid in self._sessions]

    async def remove_session(self, session_id: str) -> None:
        """移除会话并检查是否需要关闭 server 的终端 WS."""
        agent_ws_to_disconnect: WebSocket | None = None
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is None:
                return
            session.closed.set()
            server_uuid = session.server_uuid
            ids = self._server_sessions.get(server_uuid, set())
            ids.discard(session_id)
            if not ids:
                self._server_sessions.pop(server_uuid, None)
                self._tunnel_needed[server_uuid] = False
                agent_ws_to_disconnect = self._agent_tunnels.get(server_uuid)
                logger.info(
                    "Server %s: 所有终端会话已结束, 标记 terminal_needed=false",
                    server_uuid,
                )

        if agent_ws_to_disconnect:
            try:
                await agent_ws_to_disconnect.send_json({"type": "disconnect"})
            except Exception:
                pass

    # ── Agent WS 管理 ─────────────────────────────────────────────────────────

    async def register_agent_tunnel(
        self, server_uuid: str, agent_ws: WebSocket,
    ) -> None:
        """Agent 建立终端 WS 后注册."""
        async with self._lock:
            self._agent_tunnels[server_uuid] = agent_ws
            logger.info("Terminal agent WS registered: server=%s", server_uuid)
            for sid in self._server_sessions.get(server_uuid, set()):
                session = self._sessions.get(sid)
                if session:
                    session.terminal_ready.set()

    async def unregister_agent_tunnel(self, server_uuid: str) -> None:
        """Agent 终端 WS 断开时清理."""
        async with self._lock:
            self._agent_tunnels.pop(server_uuid, None)
            for sid in list(self._server_sessions.get(server_uuid, set())):
                session = self._sessions.get(sid)
                if session:
                    session.closed.set()
            logger.info("Terminal agent WS unregistered: server=%s", server_uuid)

    def get_agent_tunnel(self, server_uuid: str) -> WebSocket | None:
        return self._agent_tunnels.get(server_uuid)

    def has_agent_tunnel(self, server_uuid: str) -> bool:
        return server_uuid in self._agent_tunnels

    # ── Report 响应辅助 ───────────────────────────────────────────────────────

    def get_terminal_response(self, server_uuid: str) -> dict | None:
        """获取给 Agent 的 terminal 下发指令."""
        needed = self._tunnel_needed.get(server_uuid)
        has_tunnel = server_uuid in self._agent_tunnels

        if needed is True and not has_tunnel:
            return {"connect": True}
        if needed is False and has_tunnel:
            self._tunnel_needed.pop(server_uuid, None)
            return {"connect": False}
        return None

    # ── 清理过期会话 ──────────────────────────────────────────────────────────

    async def cleanup_expired(self) -> None:
        """清理过期终端会话."""
        expired_ids = [
            sid for sid, s in self._sessions.items() if s.is_expired
        ]
        for sid in expired_ids:
            logger.info("Cleaning up expired terminal session: %s", sid)
            await self.remove_session(sid)


# 全局单例
terminal_manager = TerminalSessionManager()

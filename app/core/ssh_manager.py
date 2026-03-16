"""SSH 会话管理器 — Web SSH 功能的核心调度组件.

职责:
  - 维护 session_id → SSHSession 映射
  - 维护 server_uuid → agent_ws 映射（Agent 隧道 WS 连接池）
  - 维护 server_uuid → ssh_tunnel_needed 标记（report 响应下发依据）
  - 前端 WS 断开时自动清理对应 SSH 会话
  - Agent WS 断开时终止该 Agent 上所有活跃会话
  - 所有会话结束后清除 ssh_tunnel_needed 标记
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid as _uuid
from dataclasses import dataclass, field

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# ── 会话限制 ──────────────────────────────────────────────────────────────────

MAX_SESSIONS_PER_SERVER = 5
SESSION_IDLE_TIMEOUT = 600       # 无 I/O 活动 10 分钟自动断开
SESSION_MAX_DURATION = 7200      # 绝对超时 2 小时
WS_HEARTBEAT_INTERVAL = 30      # 心跳 30 秒


@dataclass
class SSHSession:
    """一个 SSH 会话的状态."""

    session_id: str
    server_uuid: str
    username: str
    mode: str = "password"           # "password" | "certificate"
    cols: int = 80
    rows: int = 24
    created_at: int = field(default_factory=lambda: int(time.time()))
    client_ip: str = "unknown"

    # 运行时填充
    user_ws: WebSocket | None = None
    # asyncssh 连接和进程在 bridge 中管理，不在此处持有
    last_activity: int = field(default_factory=lambda: int(time.time()))

    # 生命周期事件
    tunnel_ready: asyncio.Event = field(default_factory=asyncio.Event)
    tunnel_confirm: asyncio.Event = field(default_factory=asyncio.Event)
    closed: asyncio.Event = field(default_factory=asyncio.Event)

    # SSH-over-WS 桥接写端（由 agent_ssh_tunnel_ws 写入 Agent 回传数据）
    bridge_writer: asyncio.StreamWriter | None = field(default=None, init=False, repr=False)

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


class SSHSessionManager:
    """全局 SSH 会话管理器（单例）."""

    def __init__(self) -> None:
        # session_id → SSHSession
        self._sessions: dict[str, SSHSession] = {}
        # server_uuid → Agent 隧道 WebSocket
        self._agent_tunnels: dict[str, WebSocket] = {}
        # server_uuid → 是否需要 SSH 隧道（report 响应下发依据）
        self._tunnel_needed: dict[str, bool] = {}
        # server_uuid → Agent 上报的 ssh_port
        self._agent_ssh_ports: dict[str, int] = {}
        # server_uuid → set of session_ids
        self._server_sessions: dict[str, set[str]] = {}
        self._lock = asyncio.Lock()

    # ── 创建 / 查询 / 删除会话 ────────────────────────────────────────────────

    async def create_session(
        self,
        *,
        server_uuid: str,
        username: str,
        cols: int = 80,
        rows: int = 24,
        client_ip: str = "unknown",
    ) -> SSHSession:
        """创建新的 SSH 会话并标记该 server 需要隧道."""
        async with self._lock:
            # 先清理该 server 上已过期或未被使用的会话
            await self._cleanup_stale_sessions_locked(server_uuid)

            # 检查并发限制
            existing = self._server_sessions.get(server_uuid, set())
            if len(existing) >= MAX_SESSIONS_PER_SERVER:
                raise ValueError(f"Server {server_uuid} has reached max concurrent sessions")

            session_id = str(_uuid.uuid4())
            session = SSHSession(
                session_id=session_id,
                server_uuid=server_uuid,
                username=username,
                cols=cols,
                rows=rows,
                client_ip=client_ip,
            )
            self._sessions[session_id] = session
            self._server_sessions.setdefault(server_uuid, set()).add(session_id)
            # 标记 server 需要 SSH 隧道（下次 report 响应时通知 Agent）
            self._tunnel_needed[server_uuid] = True

            # 如果 Agent 隧道已连接，立即标记 tunnel_ready
            if server_uuid in self._agent_tunnels:
                session.tunnel_ready.set()
                logger.info("Session %s: Agent tunnel already connected, tunnel_ready set immediately", session_id)
            else:
                logger.info("Session %s: waiting for Agent tunnel (server=%s)", session_id, server_uuid)

            return session

    async def _cleanup_stale_sessions_locked(self, server_uuid: str) -> None:
        """清理指定 server 上过期或未连接的会话（需在持锁时调用）."""
        SESSION_CONNECT_TIMEOUT = 60  # 创建后 60 秒内前端未连接则清理
        now = int(time.time())
        ids = list(self._server_sessions.get(server_uuid, set()))
        for sid in ids:
            session = self._sessions.get(sid)
            if session is None:
                self._server_sessions.get(server_uuid, set()).discard(sid)
                continue
            # 已过期（超时或超时长上限）
            if session.is_expired or session.closed.is_set():
                logger.info("Cleanup stale session %s (expired/closed)", sid)
                self._sessions.pop(sid, None)
                self._server_sessions.get(server_uuid, set()).discard(sid)
                continue
            # 创建后超时仍无前端 WS 连接
            if session.user_ws is None and (now - session.created_at) > SESSION_CONNECT_TIMEOUT:
                logger.info("Cleanup stale session %s (no WS connected after %ds)", sid, SESSION_CONNECT_TIMEOUT)
                session.closed.set()
                self._sessions.pop(sid, None)
                self._server_sessions.get(server_uuid, set()).discard(sid)
                continue
        # 若清理后无会话，清除隧道需求
        remaining = self._server_sessions.get(server_uuid, set())
        if not remaining:
            self._server_sessions.pop(server_uuid, None)
            self._tunnel_needed[server_uuid] = False

    def get_session(self, session_id: str) -> SSHSession | None:
        return self._sessions.get(session_id)

    def get_server_sessions(self, server_uuid: str) -> list[SSHSession]:
        ids = self._server_sessions.get(server_uuid, set())
        return [self._sessions[sid] for sid in ids if sid in self._sessions]

    async def remove_session(self, session_id: str) -> None:
        """移除会话并检查是否需要关闭 server 的隧道标记."""
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
                # 该 server 上没有活跃 SSH 会话了
                self._server_sessions.pop(server_uuid, None)
                # 还要检查 SFTP 会话是否也全部结束
                sftp_remaining = self._get_sftp_session_count(server_uuid)
                if sftp_remaining == 0:
                    self._tunnel_needed[server_uuid] = False
                    agent_ws_to_disconnect = self._agent_tunnels.get(server_uuid)
                    logger.info("Server %s: 所有会话已结束 (SSH+SFTP), 标记 tunnel_needed=false", server_uuid)
                else:
                    logger.info("Server %s: SSH 会话已结束, 但仍有 %d SFTP 会话", server_uuid, sftp_remaining)

        # 在锁外通过 WS 发送 disconnect，让 Agent 的消息循环自然退出
        if agent_ws_to_disconnect:
            try:
                await agent_ws_to_disconnect.send_json({"type": "disconnect"})
            except Exception:
                pass

    def _get_sftp_session_count(self, server_uuid: str) -> int:
        """获取指定 server 上的 SFTP 会话数（延迟导入避免循环引用）."""
        try:
            from app.core.sftp_manager import sftp_manager
            return len(sftp_manager._server_sessions.get(server_uuid, set()))
        except ImportError:
            return 0

    # ── Agent 隧道管理 ────────────────────────────────────────────────────────

    async def register_agent_tunnel(
        self, server_uuid: str, agent_ws: WebSocket, ssh_port: int = 22,
    ) -> None:
        """Agent 建立隧道 WS 后注册."""
        async with self._lock:
            self._agent_tunnels[server_uuid] = agent_ws
            self._agent_ssh_ports[server_uuid] = ssh_port
            logger.info("Agent tunnel registered: server=%s, ssh_port=%d", server_uuid, ssh_port)
            # 通知所有 pending SSH 会话隧道已就绪
            for sid in self._server_sessions.get(server_uuid, set()):
                session = self._sessions.get(sid)
                if session:
                    session.tunnel_ready.set()
            # 通知所有 pending SFTP 会话隧道已就绪
            try:
                from app.core.sftp_manager import sftp_manager
                for sid in sftp_manager._server_sessions.get(server_uuid, set()):
                    sf = sftp_manager._sessions.get(sid)
                    if sf:
                        sf.tunnel_ready.set()
            except ImportError:
                pass

    async def unregister_agent_tunnel(self, server_uuid: str) -> None:
        """Agent 隧道 WS 断开时清理."""
        async with self._lock:
            self._agent_tunnels.pop(server_uuid, None)
            self._agent_ssh_ports.pop(server_uuid, None)
            # 终止该 Agent 上所有活跃 SSH 会话
            for sid in list(self._server_sessions.get(server_uuid, set())):
                session = self._sessions.get(sid)
                if session:
                    session.closed.set()
            # 终止该 Agent 上所有活跃 SFTP 会话
            try:
                from app.core.sftp_manager import sftp_manager
                for sid in list(sftp_manager._server_sessions.get(server_uuid, set())):
                    sf = sftp_manager._sessions.get(sid)
                    if sf:
                        sf.closed.set()
            except ImportError:
                pass
            logger.info("Agent tunnel unregistered: server=%s", server_uuid)

    def get_agent_tunnel(self, server_uuid: str) -> WebSocket | None:
        return self._agent_tunnels.get(server_uuid)

    def has_agent_tunnel(self, server_uuid: str) -> bool:
        return server_uuid in self._agent_tunnels

    # ── Report 响应辅助 ───────────────────────────────────────────────────────

    def get_ssh_tunnel_response(self, server_uuid: str) -> dict | None:
        """获取给 Agent 的 ssh_tunnel 下发指令.

        返回值:
          - {"connect": True}  — 需要建立隧道 WS
          - {"connect": False} — 需要断开隧道 WS
          - None               — 维持当前状态
        """
        needed = self._tunnel_needed.get(server_uuid)
        has_tunnel = server_uuid in self._agent_tunnels

        if needed is True and not has_tunnel:
            logger.debug("ssh_tunnel_response: server=%s → connect=True (tunnel needed, not connected)", server_uuid)
            return {"connect": True}
        if needed is False and has_tunnel:
            # 已通知断开后清除标记，避免重复下发
            self._tunnel_needed.pop(server_uuid, None)
            logger.debug("ssh_tunnel_response: server=%s → connect=False (no sessions, tunnel alive)", server_uuid)
            return {"connect": False}
        # needed=True 且隧道已建立，或 needed=None / needed=False 且无隧道
        return None

    # ── 清理过期会话 ──────────────────────────────────────────────────────────

    async def cleanup_expired(self) -> None:
        """清理过期会话（由后台任务周期性调用）."""
        expired_ids = [
            sid for sid, s in self._sessions.items() if s.is_expired
        ]
        for sid in expired_ids:
            logger.info("Cleaning up expired SSH session: %s", sid)
            await self.remove_session(sid)


# 全局单例
ssh_manager = SSHSessionManager()

"""WebSocket 连接管理器.

管理所有前端面板的 WebSocket 连接，提供广播能力。
支持两种广播类型:
  - nodes: 节点列表与分组信息（服务器增删改时触发）
  - status: 定时状态快照推送
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket


class WSManager:
    """WebSocket 连接管理器 — 管理面板用户的实时连接."""

    def __init__(self) -> None:
        # WebSocket → authenticated（是否可见隐藏服务器）
        self._connections: dict[WebSocket, bool] = {}
        self._lock = asyncio.Lock()

    @property
    def has_connections(self) -> bool:
        """是否有活跃连接."""
        return len(self._connections) > 0

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    async def connect(self, ws: WebSocket, *, authenticated: bool = False) -> None:
        """接受并注册一个 WebSocket 连接."""
        await ws.accept()
        async with self._lock:
            self._connections[ws] = authenticated

    async def disconnect(self, ws: WebSocket) -> None:
        """移除一个已断开的连接."""
        async with self._lock:
            self._connections.pop(ws, None)

    async def broadcast(self, public_data: dict[str, Any], full_data: dict[str, Any]) -> None:
        """向所有连接广播数据.

        - 已认证连接接收 full_data（含隐藏服务器）
        - 未认证连接接收 public_data（仅非隐藏服务器）
        自动清理发送失败（已断开）的连接。
        """
        if not self._connections:
            return

        dead: list[WebSocket] = []

        async def _send(ws: WebSocket, authenticated: bool) -> None:
            try:
                await ws.send_json(full_data if authenticated else public_data)
            except Exception:
                dead.append(ws)

        await asyncio.gather(
            *(_send(ws, auth) for ws, auth in self._connections.items()),
            return_exceptions=True,
        )

        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.pop(ws, None)

    async def send_to(self, ws: WebSocket, data: dict[str, Any]) -> None:
        """向单个连接发送数据."""
        try:
            await ws.send_json(data)
        except Exception:
            await self.disconnect(ws)

    def is_authenticated(self, ws: WebSocket) -> bool:
        """查询连接是否已认证."""
        return self._connections.get(ws, False)


# 全局单例
ws_manager = WSManager()

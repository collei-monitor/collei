"""WebSocket 连接管理器.

管理所有前端面板的 WebSocket 连接，提供广播能力。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket


class WSManager:
    """WebSocket 连接管理器 — 管理面板用户的实时连接."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    @property
    def has_connections(self) -> bool:
        """是否有活跃连接."""
        return len(self._connections) > 0

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    async def connect(self, ws: WebSocket) -> None:
        """接受并注册一个 WebSocket 连接."""
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        """移除一个已断开的连接."""
        async with self._lock:
            self._connections.discard(ws)

    async def broadcast(self, data: dict[str, Any]) -> None:
        """向所有连接广播 JSON 数据.

        自动清理发送失败（已断开）的连接。
        """
        if not self._connections:
            return

        dead: list[WebSocket] = []

        async def _send(ws: WebSocket) -> None:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)

        await asyncio.gather(
            *(_send(ws) for ws in self._connections),
            return_exceptions=True,
        )

        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)


# 全局单例
ws_manager = WSManager()

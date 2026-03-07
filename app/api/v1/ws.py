"""WebSocket 路由 — 面板实时数据推送.

端点:
  WS  /api/v1/ws   管理面板 WebSocket 连接
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.ws_manager import ws_manager

router = APIRouter(tags=["websocket"])


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """面板 WebSocket 连接.

    连接后自动接收服务器状态快照广播，无需发送消息。
    前端可发送 JSON 消息进行交互（保留扩展，目前忽略）。
    """
    await ws_manager.connect(ws)
    try:
        # 保持连接存活，接收客户端消息（当前仅做心跳保活）
        while True:
            # 等待客户端消息（或检测断开）
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(ws)

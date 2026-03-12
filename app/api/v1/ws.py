"""WebSocket 路由 — 面板实时数据推送.

端点:
  WS  /api/v1/ws?token=<ws_token>   管理面板 WebSocket 连接
"""

from __future__ import annotations

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core.security import decode_ws_token
from app.core.server_cache import server_cache
from app.core.ws_manager import ws_manager

router = APIRouter(tags=["websocket"])


@router.websocket("/ws")
async def websocket_endpoint(
    ws: WebSocket,
    token: str | None = Query(None, description="由 GET /auth/me 返回的 ws_token"),
):
    """面板 WebSocket 连接.

    前端通过 URL query param 传入 ws_token（由 GET /auth/me 颁发，60 秒有效）。
    - 认证成功：推送全部在线服务器（含隐藏）。
    - 认证失败 / 未提供 token：推送在线的非隐藏服务器。
    """
    authenticated = bool(token and decode_ws_token(token))

    await ws_manager.connect(ws)
    try:
        # 连接成功后立即推送服务器快照
        await ws.send_json(server_cache.build_snapshot(include_hidden=authenticated))

        # 保持连接存活，接收客户端消息（心跳保活）
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(ws)

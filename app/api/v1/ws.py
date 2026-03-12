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
    token: str = Query(..., description="由 GET /auth/me 返回的 ws_token"),
):
    """面板 WebSocket 连接.

    前端通过 URL query param 传入 ws_token（由 GET /auth/me 颁发，60 秒有效）。
    认证通过后立即推送全量服务器快照，后续持续接收广播更新。
    """
    user_uuid = decode_ws_token(token)
    if not user_uuid:
        await ws.close(code=1008)  # Policy Violation — token 无效或已过期
        return

    await ws_manager.connect(ws)
    try:
        # 连接成功后立即推送全量服务器快照
        await ws.send_json(server_cache.build_snapshot())

        # 保持连接存活，接收客户端消息（心跳保活）
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(ws)

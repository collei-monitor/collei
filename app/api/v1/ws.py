"""WebSocket 路由 — 面板实时数据推送.

端点:
  WS  /api/v1/ws?token=<ws_token>   管理面板 WebSocket 连接

协议:
  下行（服务端 → 客户端）:
    type="nodes"   — 节点列表与分组信息（首次连接 / 服务器变更 / 客户端主动请求）
    type="status"  — 定时状态快照推送（以 uuid 为键的字典）
    type="pong"    — 心跳回复

  上行（客户端 → 服务端）:
    action="get_nodes" — 主动请求节点列表
    action="ping"      — 心跳
"""

from __future__ import annotations

import json
import time

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
    - 认证成功：推送全部服务器（含隐藏）。
    - 认证失败 / 未提供 token：推送非隐藏服务器。
    """
    authenticated = bool(token and decode_ws_token(token))

    await ws_manager.connect(ws, authenticated=authenticated)
    try:
        # 连接成功后立即推送节点列表（type="nodes"）
        nodes = server_cache.build_nodes(include_hidden=authenticated)
        await ws.send_json(nodes)

        # 消息处理循环
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            action = msg.get("action") if isinstance(msg, dict) else None

            if action == "ping":
                await ws.send_json({
                    "type": "pong",
                    "timestamp": int(time.time()),
                })
            elif action == "get_nodes":
                nodes = server_cache.build_nodes(include_hidden=authenticated)
                await ws.send_json(nodes)
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(ws)

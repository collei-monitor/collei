"""Web Terminal (ConPTY) WebSocket 路由.

端点:
  WS  /api/v1/ws/terminal?token=<jwt>&session_id=<sid>     前端终端 WebSocket
  WS  /api/v1/agent/ws/terminal?token=<agent_token>         Agent 终端 WebSocket

与 SSH 隧道模式不同，Backend 作为纯 WS 中继：
  Frontend ↔ Backend (relay) ↔ Agent (ConPTY)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import authenticate_ws_connection
from app.core.ca_manager import sign_control_frame
from app.core.server_cache import server_cache
from app.core.terminal_manager import terminal_manager
from app.db.session import get_async_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket-terminal"])


# ── 前端终端 WebSocket ────────────────────────────────────────────────────────

@router.websocket("/ws/terminal")
async def frontend_terminal_ws(
    ws: WebSocket,
    token: str | None = Query(None),
    session_id: str | None = Query(None),
    db: AsyncSession = Depends(get_async_session),
):
    """前端终端 WebSocket — 纯中继模式.

    协议:
      下行: connected / output(binary) / error / closed / pong
      上行: resize / close / ping / input(binary)
    """
    user_uuid = await authenticate_ws_connection(ws, token, db)
    if not user_uuid:
        await ws.close(code=4001, reason="Unauthorized")
        return

    if not session_id:
        await ws.close(code=4002, reason="Missing session_id")
        return

    session = terminal_manager.get_session(session_id)
    if session is None:
        await ws.close(code=4004, reason="Session not found")
        return

    await ws.accept()
    session.user_ws = ws

    try:
        # 等待 Agent 终端 WS 就绪
        if not session.terminal_ready.is_set():
            logger.info("Terminal WS: session=%s waiting for Agent WS...", session_id)
        try:
            await asyncio.wait_for(session.terminal_ready.wait(), timeout=30)
        except asyncio.TimeoutError:
            await ws.send_json({"type": "error", "message": "Agent terminal timeout"})
            return

        if session.closed.is_set():
            await ws.send_json({"type": "closed", "reason": "session_terminated"})
            return

        agent_ws = terminal_manager.get_agent_tunnel(session.server_uuid)
        if not agent_ws:
            await ws.send_json({"type": "error", "message": "Agent terminal lost"})
            return

        # 发送 open_terminal 到 Agent（带签名）
        sig = await sign_control_frame("open_terminal", session.session_id)
        await agent_ws.send_json({
            "type": "open_terminal",
            "session_id": session.session_id,
            "cols": session.cols,
            "rows": session.rows,
            "shell": session.shell,
            **sig,
        })

        # 等待 Agent 返回 terminal_ready（由 agent WS 消息循环触发）
        # terminal_ready 已在 register_agent_tunnel 中 set，这里复用，
        # 但实际的 open 确认需要额外等待 — 使用 closed event 作为超时检测
        await ws.send_json({
            "type": "connected",
            "cols": session.cols,
            "rows": session.rows,
        })

        # 双向中继
        await _relay_terminal(ws, session, agent_ws)

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("Terminal session error: %s", exc)
        try:
            await ws.send_json({"type": "error", "message": "Internal server error"})
        except Exception:
            pass
    finally:
        session.user_ws = None
        # 通知 Agent 关闭此终端会话
        agent_ws = terminal_manager.get_agent_tunnel(session.server_uuid)
        if agent_ws:
            try:
                await agent_ws.send_json({
                    "type": "close_session",
                    "session_id": session.session_id,
                })
            except Exception:
                pass
        await terminal_manager.remove_session(session_id)


async def _relay_terminal(
    user_ws: WebSocket,
    session,
    agent_ws: WebSocket,
) -> None:
    """前端 ↔ Agent 双向中继终端 I/O."""

    async def _user_to_agent():
        """前端输入 → Agent."""
        try:
            while not session.closed.is_set():
                raw = await user_ws.receive()
                msg_type = raw.get("type", "")

                if msg_type == "websocket.disconnect":
                    break

                if "bytes" in raw and raw["bytes"]:
                    session.touch()
                    # 发送 data 帧头 + binary payload
                    await agent_ws.send_text(json.dumps({
                        "type": "data",
                        "session_id": session.session_id,
                    }))
                    await agent_ws.send_bytes(raw["bytes"])

                elif "text" in raw and raw["text"]:
                    try:
                        msg = json.loads(raw["text"])
                    except (json.JSONDecodeError, TypeError):
                        continue

                    action = msg.get("action")
                    if action == "resize":
                        cols = msg.get("cols", session.cols)
                        rows = msg.get("rows", session.rows)
                        session.cols = cols
                        session.rows = rows
                        await agent_ws.send_json({
                            "type": "resize",
                            "session_id": session.session_id,
                            "cols": cols,
                            "rows": rows,
                        })
                    elif action == "close":
                        break
                    elif action == "ping":
                        await user_ws.send_json({
                            "type": "pong",
                            "timestamp": int(time.time()),
                        })
        except Exception:
            pass

    async def _watch_closed():
        """等待会话关闭事件."""
        await session.closed.wait()

    done, pending = await asyncio.wait(
        [
            asyncio.create_task(_user_to_agent()),
            asyncio.create_task(_watch_closed()),
        ],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()

    try:
        await user_ws.send_json({"type": "closed", "reason": "session_ended"})
    except Exception:
        pass


# ── Agent 终端 WebSocket ─────────────────────────────────────────────────────

@router.websocket("/agent/ws/terminal")
async def agent_terminal_ws(
    ws: WebSocket,
    token: str | None = Query(None),
):
    """Agent 终端 WebSocket.

    Agent 收到 /agent/report 响应中的 terminal: {connect: true} 后，
    主动建立此 WS 连接。一条 WS 连接复用多个终端会话。

    协议:
      下行: open_terminal / data / resize / close_session / disconnect / ping
      上行: capabilities / terminal_ready / data / terminal_closed / error / pong
    """
    if not token:
        await ws.close(code=4001, reason="Missing token")
        return

    server_uuid = server_cache.get_uuid_by_token(token)
    if not server_uuid:
        await ws.close(code=4001, reason="Invalid token")
        return

    await ws.accept()
    logger.info("Agent terminal WS connected: server=%s", server_uuid)

    try:
        # 等待 capabilities 首帧
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
            msg = json.loads(raw)
            if msg.get("type") == "capabilities":
                logger.info(
                    "Agent terminal capabilities: server=%s mode=%s shell=%s",
                    server_uuid,
                    msg.get("terminal_mode", "unknown"),
                    msg.get("default_shell", "unknown"),
                )
        except (asyncio.TimeoutError, json.JSONDecodeError):
            pass

        # 注册 Agent WS
        await terminal_manager.register_agent_tunnel(server_uuid, ws)

        # 消息循环
        _pending_sid: str | None = None
        while True:
            raw = await ws.receive()
            msg_type = raw.get("type", "")

            if msg_type == "websocket.disconnect":
                break

            if "text" in raw and raw["text"]:
                try:
                    msg = json.loads(raw["text"])
                except (json.JSONDecodeError, TypeError):
                    continue

                frame_type = msg.get("type")

                if frame_type == "terminal_ready":
                    sid = msg.get("session_id")
                    if sid:
                        s = terminal_manager.get_session(sid)
                        if s:
                            logger.debug("Agent terminal_ready for session=%s", sid)

                elif frame_type == "data":
                    # Agent → 前端: 终端输出帧头，下一个 binary 帧是数据
                    _pending_sid = msg.get("session_id")

                elif frame_type == "terminal_closed":
                    sid = msg.get("session_id")
                    if sid:
                        s = terminal_manager.get_session(sid)
                        if s:
                            reason = msg.get("reason", "terminal_closed")
                            exit_code = msg.get("exit_code")
                            if s.user_ws:
                                try:
                                    close_msg: dict = {
                                        "type": "closed",
                                        "reason": reason,
                                    }
                                    if exit_code is not None:
                                        close_msg["exit_code"] = exit_code
                                    await s.user_ws.send_json(close_msg)
                                except Exception:
                                    pass
                            await terminal_manager.remove_session(sid)

                elif frame_type == "error":
                    sid = msg.get("session_id")
                    if sid:
                        s = terminal_manager.get_session(sid)
                        if s and s.user_ws:
                            try:
                                await s.user_ws.send_json({
                                    "type": "error",
                                    "message": msg.get("error", "Agent error"),
                                })
                            except Exception:
                                pass

                elif frame_type == "pong":
                    pass

            elif "bytes" in raw and raw["bytes"]:
                # Agent → 前端: 终端输出 binary payload
                if _pending_sid:
                    s = terminal_manager.get_session(_pending_sid)
                    if s and s.user_ws:
                        try:
                            s.touch()
                            await s.user_ws.send_bytes(raw["bytes"])
                        except Exception:
                            pass
                    _pending_sid = None

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("Agent terminal WS error: %s", exc)
    finally:
        await terminal_manager.unregister_agent_tunnel(server_uuid)
        logger.info("Agent terminal WS disconnected: server=%s", server_uuid)

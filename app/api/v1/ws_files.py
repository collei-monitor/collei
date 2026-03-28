"""Web File API WebSocket 路由.

端点:
  WS  /api/v1/ws/files?token=<jwt>&session_id=<sid>     前端文件 WebSocket
  WS  /api/v1/agent/ws/files?token=<agent_token>         Agent 文件 WebSocket

Backend 作为纯 WS 中继：
  Frontend ↔ Backend (relay) ↔ Agent (native file API)
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
from app.core.fileapi_manager import fileapi_manager
from app.core.server_cache import server_cache
from app.db.session import get_async_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket-files"])

# 需要签名的变更操作
_SIGNED_OPS = {"write", "remove", "rename", "mkdir", "rmdir"}


# ── 前端文件 WebSocket ────────────────────────────────────────────────────────

@router.websocket("/ws/files")
async def frontend_files_ws(
    ws: WebSocket,
    token: str | None = Query(None),
    session_id: str | None = Query(None),
    db: AsyncSession = Depends(get_async_session),
):
    """前端文件 API WebSocket — 纯中继模式.

    协议:
      下行: ready / readdir_resp / stat_resp / read_resp / write_resp /
            remove_resp / rename_resp / mkdir_resp / rmdir_resp / error / closed / pong
      上行: readdir / stat / read / write / remove / rename / mkdir / rmdir /
            close / ping / (binary: 写入文件数据)
    """
    user_uuid = await authenticate_ws_connection(ws, token, db)
    if not user_uuid:
        await ws.close(code=4001, reason="Unauthorized")
        return

    if not session_id:
        await ws.close(code=4002, reason="Missing session_id")
        return

    session = fileapi_manager.get_session(session_id)
    if session is None:
        await ws.close(code=4004, reason="Session not found")
        return

    await ws.accept()
    session.user_ws = ws

    try:
        # 等待 Agent 文件 WS 就绪
        if not session.tunnel_ready.is_set():
            logger.info("Files WS: session=%s waiting for Agent WS...", session_id)
        try:
            await asyncio.wait_for(session.tunnel_ready.wait(), timeout=30)
        except asyncio.TimeoutError:
            await ws.send_json({"type": "error", "message": "Agent file API timeout"})
            return

        if session.closed.is_set():
            await ws.send_json({"type": "closed", "reason": "session_terminated"})
            return

        agent_ws = fileapi_manager.get_agent_tunnel(session.server_uuid)
        if not agent_ws:
            await ws.send_json({"type": "error", "message": "Agent file API lost"})
            return

        await ws.send_json({"type": "ready"})

        # 双向中继
        await _relay_files(ws, session, agent_ws)

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("Files session error: %s", exc)
        try:
            await ws.send_json({"type": "error", "message": "Internal server error"})
        except Exception:
            pass
    finally:
        session.user_ws = None
        await fileapi_manager.remove_session(session_id)


async def _relay_files(
    user_ws: WebSocket,
    session,
    agent_ws: WebSocket,
) -> None:
    """前端 ↔ Agent 双向中继文件操作."""

    async def _user_to_agent():
        """前端请求 → Agent."""
        try:
            while not session.closed.is_set():
                raw = await user_ws.receive()
                msg_type = raw.get("type", "")

                if msg_type == "websocket.disconnect":
                    break

                if "text" in raw and raw["text"]:
                    try:
                        msg = json.loads(raw["text"])
                    except (json.JSONDecodeError, TypeError):
                        continue

                    action = msg.get("type") or msg.get("action")
                    if not action:
                        continue

                    if action == "close":
                        break
                    elif action == "ping":
                        await user_ws.send_json({
                            "type": "pong",
                            "timestamp": int(time.time()),
                        })
                        continue

                    session.touch()

                    # 构建发往 Agent 的帧
                    frame = dict(msg)
                    frame["type"] = action
                    frame["session_id"] = session.session_id

                    # 变更操作需要 CA 签名
                    if action in _SIGNED_OPS:
                        extra = ""
                        if action == "write":
                            extra = msg.get("path", "")
                        elif action == "remove":
                            extra = msg.get("path", "")
                        elif action == "rename":
                            extra = f"{msg.get('old', '')}|{msg.get('new', '')}"
                        elif action in ("mkdir", "rmdir"):
                            extra = msg.get("path", "")

                        sig = await sign_control_frame(
                            action, session.session_id, extra,
                        )
                        frame.update(sig)

                    await agent_ws.send_text(json.dumps(frame))

                elif "bytes" in raw and raw["bytes"]:
                    # 前端 → Agent: 文件写入 binary payload
                    session.touch()
                    await agent_ws.send_bytes(raw["bytes"])

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


# ── Agent 文件 API WebSocket ─────────────────────────────────────────────────

@router.websocket("/agent/ws/files")
async def agent_files_ws(
    ws: WebSocket,
    token: str | None = Query(None),
):
    """Agent 文件 API WebSocket.

    Agent 收到 /agent/report 响应中的 file_api: {connect: true} 后，
    主动建立此 WS 连接。一条 WS 连接复用多个文件操作会话。

    协议:
      下行: readdir / stat / read / write / remove / rename / mkdir / rmdir /
            disconnect / ping
      上行: capabilities / readdir_resp / stat_resp / read_resp / write_resp /
            remove_resp / rename_resp / mkdir_resp / rmdir_resp / error / pong
    """
    if not token:
        await ws.close(code=4001, reason="Missing token")
        return

    server_uuid = server_cache.get_uuid_by_token(token)
    if not server_uuid:
        await ws.close(code=4001, reason="Invalid token")
        return

    await ws.accept()
    logger.info("Agent files WS connected: server=%s", server_uuid)

    try:
        # 等待 capabilities 首帧
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
            msg = json.loads(raw)
            if msg.get("type") == "capabilities":
                logger.info(
                    "Agent files capabilities: server=%s operations=%s",
                    server_uuid,
                    msg.get("operations", []),
                )
        except (asyncio.TimeoutError, json.JSONDecodeError):
            pass

        # 注册 Agent WS
        await fileapi_manager.register_agent_tunnel(server_uuid, ws)

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
                sid = msg.get("session_id")

                if frame_type in (
                    "readdir_resp", "stat_resp", "write_resp",
                    "remove_resp", "rename_resp", "mkdir_resp", "rmdir_resp",
                ):
                    # 非 binary 的响应直接转发给前端
                    if sid:
                        s = fileapi_manager.get_session(sid)
                        if s and s.user_ws:
                            try:
                                s.touch()
                                await s.user_ws.send_text(raw["text"])
                            except Exception:
                                pass

                elif frame_type == "read_resp":
                    # read_resp 后跟 binary 帧
                    _pending_sid = sid
                    if sid:
                        s = fileapi_manager.get_session(sid)
                        if s and s.user_ws:
                            try:
                                s.touch()
                                await s.user_ws.send_text(raw["text"])
                            except Exception:
                                pass

                elif frame_type == "error":
                    if sid:
                        s = fileapi_manager.get_session(sid)
                        if s and s.user_ws:
                            try:
                                await s.user_ws.send_text(raw["text"])
                            except Exception:
                                pass

                elif frame_type == "pong":
                    pass

            elif "bytes" in raw and raw["bytes"]:
                # Agent → 前端: 文件读取 binary payload
                if _pending_sid:
                    s = fileapi_manager.get_session(_pending_sid)
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
        logger.exception("Agent files WS error: %s", exc)
    finally:
        await fileapi_manager.unregister_agent_tunnel(server_uuid)
        logger.info("Agent files WS disconnected: server=%s", server_uuid)

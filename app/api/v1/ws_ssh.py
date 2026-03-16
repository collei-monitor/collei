"""Web SSH WebSocket 路由.

端点:
  WS  /api/v1/ws/ssh?token=<jwt>&session_id=<sid>     前端终端 WebSocket
  WS  /api/v1/agent/ws/ssh?token=<agent_token>         Agent 隧道 WebSocket
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket as _socket
import time

import asyncssh
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core.security import decode_ws_token
from app.core.server_cache import server_cache
from app.core.ssh_manager import ssh_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket-ssh"])

# ── SSH CA 密钥管理（委托 ca_manager）───────────────────────────────────────

from app.core.ca_manager import get_ca_key as _get_ca_key  # noqa: E402
from app.core.ca_manager import get_ca_public_key  # noqa: E402, F401 (re-export)


# ── socketpair 桥接辅助 ──────────────────────────────────────────────────────

def _create_tcp_socketpair() -> tuple[_socket.socket, _socket.socket]:
    """Create a pair of connected TCP sockets with valid peername.

    Unlike socket.socketpair() (AF_UNIX), these TCP sockets have valid
    peername info, which asyncssh requires in connection_made().
    """
    srv = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    srv.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    client = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    client.connect(("127.0.0.1", port))
    server_side, _ = srv.accept()
    srv.close()

    return client, server_side


async def _open_session_bridge(
    session, agent_ws: WebSocket,
) -> tuple[_socket.socket, asyncio.Task, asyncio.StreamWriter]:
    """Create a socketpair and bridge one end to the Agent WS.

    Returns (sock_for_asyncssh, bridge_task, bridge_writer).
    - sock_for_asyncssh: pass to asyncssh.create_connection(sock=...)
    - bridge_task: reads from local socket, sends to Agent WS with session framing
    - bridge_writer: Agent replies are routed here by agent_ssh_tunnel_ws
    """
    sock_ssh, sock_bridge = _create_tcp_socketpair()
    sock_ssh.setblocking(False)
    sock_bridge.setblocking(False)

    bridge_reader, bridge_writer = await asyncio.open_connection(sock=sock_bridge)
    session.bridge_writer = bridge_writer

    async def _bridge_to_agent() -> None:
        """Local socket → Agent WS (SSH protocol bytes outbound)."""
        try:
            while True:
                data = await bridge_reader.read(32768)
                if not data:
                    break
                await agent_ws.send_text(json.dumps({
                    "type": "data",
                    "session_id": session.session_id,
                }))
                await agent_ws.send_bytes(data)
        except Exception:
            pass

    bridge_task = asyncio.create_task(_bridge_to_agent())
    return sock_ssh, bridge_task, bridge_writer


def _close_bridge(
    session,
    bridge_task: asyncio.Task | None,
    bridge_writer: asyncio.StreamWriter | None,
) -> None:
    """Clean up bridge resources."""
    if bridge_task and not bridge_task.done():
        bridge_task.cancel()
    if bridge_writer:
        try:
            bridge_writer.close()
        except Exception:
            pass
    session.bridge_writer = None


# ── 前端终端 WebSocket ────────────────────────────────────────────────────────

@router.websocket("/ws/ssh")
async def frontend_ssh_ws(
    ws: WebSocket,
    token: str | None = Query(None),
    session_id: str | None = Query(None),
):
    """前端终端 WebSocket.

    协议:
      下行: connected / auth_required / output(binary) / error / closed
      上行: auth / resize / close / ping / input(binary)
    """
    # 认证
    if not token or not decode_ws_token(token):
        await ws.close(code=4001, reason="Unauthorized")
        return

    if not session_id:
        await ws.close(code=4002, reason="Missing session_id")
        return

    session = ssh_manager.get_session(session_id)
    if session is None:
        await ws.close(code=4004, reason="Session not found")
        return

    await ws.accept()
    session.user_ws = ws

    try:
        # 等待 Agent 隧道就绪（最多等 30 秒）
        if session.tunnel_ready.is_set():
            logger.info("SSH WS: session=%s tunnel already ready", session_id)
        else:
            logger.info("SSH WS: session=%s waiting for Agent tunnel...", session_id)
        try:
            await asyncio.wait_for(session.tunnel_ready.wait(), timeout=30)
        except asyncio.TimeoutError:
            logger.warning(
                "SSH WS: session=%s tunnel timeout (server=%s, has_tunnel=%s, tunnel_needed=%s)",
                session_id, session.server_uuid,
                ssh_manager.has_agent_tunnel(session.server_uuid),
                ssh_manager._tunnel_needed.get(session.server_uuid),
            )
            await ws.send_json({"type": "error", "message": "Agent tunnel timeout"})
            return

        if session.closed.is_set():
            await ws.send_json({"type": "closed", "reason": "session_terminated"})
            return

        # Agent 隧道已就绪，开始 SSH 连接
        agent_ws = ssh_manager.get_agent_tunnel(session.server_uuid)
        if not agent_ws:
            await ws.send_json({"type": "error", "message": "Agent tunnel lost"})
            return

        # 尝试 SSH 连接
        await _do_ssh_connect(ws, session, agent_ws)

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("SSH session error: %s", exc)
        try:
            await ws.send_json({"type": "error", "message": "Internal server error"})
        except Exception:
            pass
    finally:
        session.user_ws = None
        await ssh_manager.remove_session(session_id)


async def _request_agent_tunnel(
    session, agent_ws: WebSocket,
) -> bool:
    """Send open_tunnel to Agent and wait for tunnel_confirm."""
    session.tunnel_confirm.clear()
    await agent_ws.send_json({
        "type": "open_tunnel",
        "session_id": session.session_id,
    })
    try:
        await asyncio.wait_for(session.tunnel_confirm.wait(), timeout=15)
        return True
    except asyncio.TimeoutError:
        return False


async def _do_ssh_connect(
    user_ws: WebSocket,
    session,
    agent_ws: WebSocket,
) -> None:
    """Execute SSH connection with certificate-first, password-fallback strategy.

    Critical ordering: bridge must be created BEFORE open_tunnel so that
    sshd's banner (sent immediately on TCP connect) is buffered in the
    socketpair and available when asyncssh starts the handshake.

    Flow:
      1. Create bridge + open Agent TCP tunnel
      2. Try certificate auth (silent, no user interaction)
      3. If cert auth fails → close bridge/tunnel → reopen → ask password
    """
    # ── Phase 1: Certificate auth (silent attempt) ────────────────────

    sock_ssh, bridge_task, bridge_writer = await _open_session_bridge(session, agent_ws)

    try:
        if not await _request_agent_tunnel(session, agent_ws):
            await user_ws.send_json({"type": "error", "message": "Tunnel open timeout"})
            return

        cert_ok = False
        conn: asyncssh.SSHClientConnection | None = None
        try:
            ca_key = await _get_ca_key()
            user_key = asyncssh.generate_private_key("ssh-ed25519")
            now = int(time.time())
            cert = ca_key.generate_user_certificate(
                user_key,
                key_id=f"collei-webssh-{session.session_id}",
                principals=[session.username],
                valid_after=now - 60,
                valid_before=now + 300,
            )

            conn, _ = await asyncssh.create_connection(
                asyncssh.SSHClient,
                sock=sock_ssh,
                username=session.username,
                client_keys=[(user_key, cert)],
                known_hosts=None,
            )
            cert_ok = True
            session.mode = "certificate"
            logger.info("Session %s: certificate auth succeeded", session.session_id)
        except (asyncssh.PermissionDenied, asyncssh.DisconnectError, asyncssh.KeyImportError, OSError) as exc:
            logger.info("Session %s: certificate auth failed (%s), falling back to password", session.session_id, exc)

        if cert_ok and conn:
            try:
                await _run_ssh_session(user_ws, session, conn)
            finally:
                conn.close()
                await conn.wait_closed()
            return

    finally:
        _close_bridge(session, bridge_task, bridge_writer)
        try:
            await agent_ws.send_json({
                "type": "close_session",
                "session_id": session.session_id,
            })
        except Exception:
            pass

    # ── Phase 2: Password auth (interactive) ──────────────────────────

    # Need fresh bridge + tunnel since the cert attempt consumed the previous ones
    await agent_ws.send_json({
        "type": "close_session",
        "session_id": session.session_id,
    })
    await asyncio.sleep(0.2)

    sock_ssh, bridge_task, bridge_writer = await _open_session_bridge(session, agent_ws)

    try:
        if not await _request_agent_tunnel(session, agent_ws):
            await user_ws.send_json({"type": "error", "message": "Tunnel open timeout"})
            return

        await user_ws.send_json({
            "type": "auth_required",
            "methods": ["password"],
        })
        session.mode = "password"

        while True:
            raw = await user_ws.receive()
            if raw.get("type") == "websocket.disconnect":
                return

            text = raw.get("text")
            if not text:
                continue

            try:
                msg = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                continue

            if msg.get("action") != "auth":
                continue

            username = msg.get("username", session.username)
            password = msg.get("password")
            if not password:
                await user_ws.send_json({"type": "error", "message": "Password required"})
                continue

            try:
                conn, _ = await asyncssh.create_connection(
                    asyncssh.SSHClient,
                    sock=sock_ssh,
                    username=username,
                    password=password,
                    known_hosts=None,
                )
            except asyncssh.PermissionDenied:
                # Auth failed — need fresh TCP tunnel + bridge for retry
                _close_bridge(session, bridge_task, bridge_writer)

                await agent_ws.send_json({
                    "type": "close_session",
                    "session_id": session.session_id,
                })
                await asyncio.sleep(0.3)

                sock_ssh, bridge_task, bridge_writer = await _open_session_bridge(session, agent_ws)

                if not await _request_agent_tunnel(session, agent_ws):
                    await user_ws.send_json({"type": "error", "message": "Tunnel reopen failed"})
                    return

                await user_ws.send_json({"type": "error", "message": "Authentication failed"})
                continue
            except (asyncssh.DisconnectError, OSError) as exc:
                await user_ws.send_json({"type": "error", "message": str(exc)})
                return

            session.username = username
            try:
                await _run_ssh_session(user_ws, session, conn)
            finally:
                conn.close()
                await conn.wait_closed()
            return

    finally:
        _close_bridge(session, bridge_task, bridge_writer)
        try:
            await agent_ws.send_json({
                "type": "close_session",
                "session_id": session.session_id,
            })
        except Exception:
            pass


async def _run_ssh_session(
    user_ws: WebSocket,
    session,
    conn: asyncssh.SSHClientConnection,
) -> None:
    """建立 PTY 并桥接终端 I/O."""
    try:
        proc = await conn.create_process(
            term_type="xterm-256color",
            term_size=(session.cols, session.rows),
            encoding=None,
        )

        await user_ws.send_json({
            "type": "connected",
            "cols": session.cols,
            "rows": session.rows,
        })

        # 双向桥接
        async def _read_ssh_stdout():
            """SSH stdout → 前端."""
            try:
                while True:
                    data = await proc.stdout.read(32768)
                    if not data:
                        break
                    session.touch()
                    await user_ws.send_bytes(data)
            except Exception:
                pass

        async def _read_user_input():
            """前端输入 → SSH stdin."""
            try:
                while True:
                    raw = await user_ws.receive()
                    msg_type = raw.get("type", "")

                    if msg_type == "websocket.disconnect":
                        break

                    if "bytes" in raw and raw["bytes"]:
                        session.touch()
                        proc.stdin.write(raw["bytes"])
                        await proc.stdin.drain()

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
                            proc.change_terminal_size(cols, rows)
                        elif action == "close":
                            break
                        elif action == "ping":
                            await user_ws.send_json({
                                "type": "pong",
                                "timestamp": int(time.time()),
                            })
            except Exception:
                pass

        # 并行运行两个方向的桥接
        done, pending = await asyncio.wait(
            [
                asyncio.create_task(_read_ssh_stdout()),
                asyncio.create_task(_read_user_input()),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()

        await user_ws.send_json({"type": "closed", "reason": "session_ended"})

    except Exception as exc:
        logger.exception("SSH session bridge error: %s", exc)


# ── Agent 隧道 WebSocket ─────────────────────────────────────────────────────

@router.websocket("/agent/ws/ssh")
async def agent_ssh_tunnel_ws(
    ws: WebSocket,
    token: str | None = Query(None),
):
    """Agent SSH 隧道 WebSocket.

    Agent 收到 /agent/report 响应中的 ssh_tunnel: {connect: true} 后，
    主动建立此 WS 连接。一条 WS 连接复用多个 SSH 会话。

    协议:
      下行: open_tunnel / data / close_session / disconnect / ping
      上行: capabilities / tunnel_ready / data / tunnel_closed / pong
    """
    if not token:
        await ws.close(code=4001, reason="Missing token")
        return

    # 通过 token 查找 server
    server_uuid = server_cache.get_uuid_by_token(token)
    if not server_uuid:
        await ws.close(code=4001, reason="Invalid token")
        return

    await ws.accept()
    logger.info("Agent SSH tunnel connected: server=%s", server_uuid)

    ssh_port = 22  # 默认值，等 capabilities 帧更新

    try:
        # 等待 capabilities 首帧
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
            msg = json.loads(raw)
            if msg.get("type") == "capabilities":
                ssh_port = msg.get("ssh_port", 22)
        except (asyncio.TimeoutError, json.JSONDecodeError):
            pass

        # 注册隧道
        await ssh_manager.register_agent_tunnel(server_uuid, ws, ssh_port)

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

                if frame_type == "tunnel_ready":
                    # Agent 确认 TCP 隧道已建立
                    sid = msg.get("session_id")
                    if sid:
                        s = ssh_manager.get_session(sid)
                        if s:
                            s.tunnel_confirm.set()
                            logger.debug("Agent tunnel_ready for session=%s", sid)
                        else:
                            # 可能是 SFTP 会话
                            from app.core.sftp_manager import sftp_manager
                            sf = sftp_manager.get_session(sid)
                            if sf:
                                sf.tunnel_confirm.set()
                                logger.debug("Agent tunnel_ready for SFTP session=%s", sid)

                elif frame_type == "data":
                    # Agent → Backend: SSH 数据帧 header
                    # 下一个 binary 帧携带实际数据
                    _pending_sid = msg.get("session_id")

                elif frame_type == "tunnel_closed":
                    sid = msg.get("session_id")
                    reason = msg.get("reason", "tunnel_closed")
                    if sid:
                        s = ssh_manager.get_session(sid)
                        if s and s.user_ws:
                            try:
                                await s.user_ws.send_json({
                                    "type": "closed",
                                    "reason": reason,
                                })
                            except Exception:
                                pass
                            await ssh_manager.remove_session(sid)
                        else:
                            # 可能是 SFTP 会话
                            from app.core.sftp_manager import sftp_manager
                            sf = sftp_manager.get_session(sid)
                            if sf and sf.user_ws:
                                try:
                                    await sf.user_ws.send_json({
                                        "type": "closed",
                                        "reason": reason,
                                    })
                                except Exception:
                                    pass
                            await sftp_manager.remove_session(sid)

                elif frame_type == "pong":
                    pass

            elif "bytes" in raw and raw["bytes"]:
                # Agent → Backend: SSH 数据帧 binary payload
                # 路由到对应 session 的 bridge_writer（SSH 或 SFTP）
                if _pending_sid:
                    s = ssh_manager.get_session(_pending_sid)
                    if not s:
                        from app.core.sftp_manager import sftp_manager
                        s = sftp_manager.get_session(_pending_sid)
                    if s and s.bridge_writer:
                        try:
                            s.bridge_writer.write(raw["bytes"])
                            await s.bridge_writer.drain()
                        except Exception:
                            pass
                    _pending_sid = None

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("Agent SSH tunnel error: %s", exc)
    finally:
        await ssh_manager.unregister_agent_tunnel(server_uuid)
        logger.info("Agent SSH tunnel disconnected: server=%s", server_uuid)

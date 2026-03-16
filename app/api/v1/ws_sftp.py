"""Web SFTP WebSocket 路由.

端点:
  WS  /api/v1/ws/sftp?token=<jwt>&session_id=<sid>     前端 SFTP WebSocket
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import time

import asyncssh
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core.security import decode_ws_token
from app.core.sftp_manager import SFTPSession, sftp_manager
from app.core.ssh_manager import ssh_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket-sftp"])

# 上传大小限制（100 MB）
MAX_UPLOAD_SIZE = 100 * 1024 * 1024
# 下载分块大小（64 KB）
DOWNLOAD_CHUNK_SIZE = 65536
# 上传进度推送间隔（每 256 KB 推送一次）
UPLOAD_PROGRESS_INTERVAL = 256 * 1024


def _format_permissions(mode: int) -> str:
    """将数字权限转换为 rwxrwxrwx 形式的字符串."""
    parts = []
    for who in (6, 3, 0):  # owner, group, other
        r = "r" if mode & (4 << who) else "-"
        w = "w" if mode & (2 << who) else "-"
        x = "x" if mode & (1 << who) else "-"
        parts.append(r + w + x)
    return "".join(parts)


def _entry_type(attrs: asyncssh.SFTPAttrs) -> str:
    """判断文件类型."""
    if attrs.permissions is None:
        return "file"
    if stat.S_ISDIR(attrs.permissions):
        return "dir"
    if stat.S_ISLNK(attrs.permissions):
        return "link"
    return "file"


async def _build_entry(
    sftp: asyncssh.SFTPClient, parent: str, name: str, attrs: asyncssh.SFTPAttrs,
) -> dict:
    """构建单个目录条目."""
    entry = {
        "name": name,
        "type": _entry_type(attrs),
        "size": attrs.size or 0,
        "permissions": _format_permissions(attrs.permissions & 0o777) if attrs.permissions is not None else "?????????",
        "owner": str(attrs.uid) if attrs.uid is not None else "",
        "group": str(attrs.gid) if attrs.gid is not None else "",
        "mtime": int(attrs.mtime) if attrs.mtime is not None else 0,
    }
    # 如果是符号链接，读取目标路径
    if entry["type"] == "link":
        try:
            target = await sftp.readlink(os.path.join(parent, name))
            entry["link_target"] = target
            # 获取链接目标的真实属性来判断原始类型
            try:
                real_attrs = await sftp.stat(os.path.join(parent, name))
                if real_attrs.permissions is not None and stat.S_ISDIR(real_attrs.permissions):
                    entry["type"] = "dir"
            except (asyncssh.SFTPError, OSError):
                pass
        except (asyncssh.SFTPError, OSError):
            pass
    return entry


# ── 从 ws_ssh 复用 bridge / tunnel 辅助函数 ──────────────────────────────────

from app.api.v1.ws_ssh import (
    _close_bridge,
    _open_session_bridge,
    _request_agent_tunnel,
)
from app.core.ca_manager import get_ca_key as _get_ca_key


# ── 前端 SFTP WebSocket ─────────────────────────────────────────────────────

@router.websocket("/ws/sftp")
async def frontend_sftp_ws(
    ws: WebSocket,
    token: str | None = Query(None),
    session_id: str | None = Query(None),
):
    """前端 SFTP WebSocket.

    协议:
      下行: ready / auth_required / ls / stat / download_start / download_end /
            upload_progress / ok / error / closed / pong
      上行: auth / ls / stat / download / upload / mkdir / rm / rename /
            close / ping / (binary: 上传分块)
    """
    if not token or not decode_ws_token(token):
        await ws.close(code=4001, reason="Unauthorized")
        return

    if not session_id:
        await ws.close(code=4002, reason="Missing session_id")
        return

    session = sftp_manager.get_session(session_id)
    if session is None:
        await ws.close(code=4004, reason="Session not found")
        return

    await ws.accept()
    session.user_ws = ws

    try:
        # 等待 Agent 隧道就绪
        if not session.tunnel_ready.is_set():
            logger.info("SFTP WS: session=%s waiting for Agent tunnel...", session_id)
        try:
            await asyncio.wait_for(session.tunnel_ready.wait(), timeout=30)
        except asyncio.TimeoutError:
            await ws.send_json({"type": "error", "message": "Agent tunnel timeout"})
            return

        if session.closed.is_set():
            await ws.send_json({"type": "closed", "reason": "session_terminated"})
            return

        agent_ws = ssh_manager.get_agent_tunnel(session.server_uuid)
        if not agent_ws:
            await ws.send_json({"type": "error", "message": "Agent tunnel lost"})
            return

        # SSH 连接 + SFTP 子系统
        await _do_sftp_connect(ws, session, agent_ws)

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("SFTP session error: %s", exc)
        try:
            await ws.send_json({"type": "error", "message": "Internal server error"})
        except Exception:
            pass
    finally:
        session.user_ws = None
        await sftp_manager.remove_session(session_id)


async def _do_sftp_connect(
    user_ws: WebSocket,
    session: SFTPSession,
    agent_ws: WebSocket,
) -> None:
    """执行 SSH 连接 + 打开 SFTP 子系统.

    认证策略: 证书优先 → 预设密码 → 交互密码输入
    """
    # ── Phase 1: 证书认证（静默尝试）────────────────────────────

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
                key_id=f"collei-sftp-{session.session_id}",
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
            logger.info("SFTP session %s: certificate auth succeeded", session.session_id)
        except (asyncssh.PermissionDenied, asyncssh.DisconnectError, asyncssh.KeyImportError, OSError) as exc:
            logger.info("SFTP session %s: certificate auth failed (%s)", session.session_id, exc)

        if cert_ok and conn:
            session.conn = conn
            session.bridge_task = bridge_task
            session.clear_password()
            try:
                sftp = await conn.start_sftp_client()
                session.sftp = sftp
                # 获取 home 目录
                try:
                    session.home_dir = await sftp.realpath(".")
                except Exception:
                    session.home_dir = "/"
                await user_ws.send_json({
                    "type": "ready",
                    "home_dir": session.home_dir,
                })
                await _sftp_message_loop(user_ws, session, sftp)
            finally:
                conn.close()
                await conn.wait_closed()
                session.conn = None
                session.sftp = None
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

    # ── Phase 2: 预设密码认证 ────────────────────────────────────

    if session.password:
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

            try:
                conn, _ = await asyncssh.create_connection(
                    asyncssh.SSHClient,
                    sock=sock_ssh,
                    username=session.username,
                    password=session.password,
                    known_hosts=None,
                )
            except asyncssh.PermissionDenied:
                logger.info("SFTP session %s: pre-set password auth failed", session.session_id)
                # 密码错误，继续到 Phase 3 交互式认证
                _close_bridge(session, bridge_task, bridge_writer)
                try:
                    await agent_ws.send_json({
                        "type": "close_session",
                        "session_id": session.session_id,
                    })
                except Exception:
                    pass
                session.clear_password()
                # 落入 Phase 3
                return await _phase3_interactive_auth(user_ws, session, agent_ws)
            except (asyncssh.DisconnectError, OSError) as exc:
                await user_ws.send_json({"type": "error", "message": str(exc)})
                return

            session.conn = conn
            session.bridge_task = bridge_task
            session.clear_password()
            try:
                sftp = await conn.start_sftp_client()
                session.sftp = sftp
                try:
                    session.home_dir = await sftp.realpath(".")
                except Exception:
                    session.home_dir = "/"
                await user_ws.send_json({
                    "type": "ready",
                    "home_dir": session.home_dir,
                })
                await _sftp_message_loop(user_ws, session, sftp)
            finally:
                conn.close()
                await conn.wait_closed()
                session.conn = None
                session.sftp = None
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

    # ── Phase 3: 交互式密码认证 ──────────────────────────────────

    session.clear_password()
    await _phase3_interactive_auth(user_ws, session, agent_ws)


async def _phase3_interactive_auth(
    user_ws: WebSocket,
    session: SFTPSession,
    agent_ws: WebSocket,
) -> None:
    """交互式密码认证阶段."""
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
                # 认证失败 — 需要新的 TCP 隧道 + bridge
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
            session.conn = conn
            session.bridge_task = bridge_task
            try:
                sftp = await conn.start_sftp_client()
                session.sftp = sftp
                try:
                    session.home_dir = await sftp.realpath(".")
                except Exception:
                    session.home_dir = "/"
                await user_ws.send_json({
                    "type": "ready",
                    "home_dir": session.home_dir,
                })
                await _sftp_message_loop(user_ws, session, sftp)
            finally:
                conn.close()
                await conn.wait_closed()
                session.conn = None
                session.sftp = None
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


# ── SFTP 消息循环 ────────────────────────────────────────────────────────────

async def _sftp_message_loop(
    user_ws: WebSocket,
    session: SFTPSession,
    sftp: asyncssh.SFTPClient,
) -> None:
    """SFTP 文件操作消息循环."""
    while True:
        raw = await user_ws.receive()
        msg_type = raw.get("type", "")

        if msg_type == "websocket.disconnect":
            break

        # Binary 帧 → 上传分块数据
        if "bytes" in raw and raw["bytes"]:
            session.touch()
            await _handle_upload_chunk(user_ws, session, sftp, raw["bytes"])
            continue

        # JSON 帧 → 文件操作
        text = raw.get("text")
        if not text:
            continue

        try:
            msg = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            continue

        action = msg.get("action")
        rid = msg.get("request_id")
        session.touch()

        try:
            if action == "ls":
                await _handle_ls(user_ws, sftp, rid, msg.get("path", "."))
            elif action == "stat":
                await _handle_stat(user_ws, sftp, rid, msg.get("path", "."))
            elif action == "download":
                await _handle_download(user_ws, sftp, session, rid, msg.get("path", ""))
            elif action == "upload":
                await _handle_upload_start(user_ws, session, sftp, msg, rid)
            elif action == "mkdir":
                await _handle_mkdir(user_ws, sftp, rid, msg.get("path", ""))
            elif action == "rm":
                await _handle_rm(user_ws, sftp, rid, msg.get("path", ""), msg.get("recursive", False))
            elif action == "rename":
                await _handle_rename(user_ws, sftp, rid, msg.get("old_path", ""), msg.get("new_path", ""))
            elif action == "close":
                await user_ws.send_json({"type": "closed", "reason": "user_close"})
                break
            elif action == "ping":
                await user_ws.send_json({"type": "pong", "timestamp": int(time.time())})
            else:
                await user_ws.send_json({
                    "type": "error",
                    "request_id": rid,
                    "message": f"Unknown action: {action}",
                })
        except Exception as exc:
            logger.warning("SFTP action '%s' error: %s", action, exc)
            await user_ws.send_json({
                "type": "error",
                "request_id": rid,
                "message": str(exc),
            })


# ── SFTP 操作处理函数 ────────────────────────────────────────────────────────

async def _handle_ls(
    ws: WebSocket, sftp: asyncssh.SFTPClient, rid: str | None, path: str,
) -> None:
    """列出目录内容."""
    try:
        items = await sftp.readdir(path)
    except asyncssh.SFTPNoSuchFile:
        await ws.send_json({
            "type": "error", "request_id": rid,
            "message": f"Directory not found: {path}",
        })
        return
    except asyncssh.SFTPPermissionDenied:
        await ws.send_json({
            "type": "error", "request_id": rid,
            "message": f"Permission denied: {path}",
        })
        return

    entries = []
    for item in items:
        raw_name = item.filename
        if raw_name in (".", "..", b".", b".."):
            continue
        name: str = raw_name.decode("utf-8", errors="replace") if isinstance(raw_name, (bytes, bytearray)) else str(raw_name)
        entries.append(await _build_entry(sftp, path, name, item.attrs))

    await ws.send_json({
        "type": "ls",
        "request_id": rid,
        "path": path,
        "entries": entries,
    })


async def _handle_stat(
    ws: WebSocket, sftp: asyncssh.SFTPClient, rid: str | None, path: str,
) -> None:
    """获取文件/目录信息."""
    try:
        attrs = await sftp.lstat(path)
    except asyncssh.SFTPNoSuchFile:
        await ws.send_json({
            "type": "error", "request_id": rid,
            "message": f"Not found: {path}",
        })
        return

    name = path.rsplit("/", 1)[-1] or path
    entry = await _build_entry(sftp, os.path.dirname(path), name, attrs)
    await ws.send_json({
        "type": "stat",
        "request_id": rid,
        "entry": entry,
    })


async def _handle_download(
    ws: WebSocket,
    sftp: asyncssh.SFTPClient,
    session: SFTPSession,
    rid: str | None,
    path: str,
) -> None:
    """流式下载文件."""
    if not path:
        await ws.send_json({
            "type": "error", "request_id": rid,
            "message": "Path is required",
        })
        return

    try:
        attrs = await sftp.stat(path)
    except asyncssh.SFTPNoSuchFile:
        await ws.send_json({
            "type": "error", "request_id": rid,
            "message": f"File not found: {path}",
        })
        return

    if attrs.permissions is not None and stat.S_ISDIR(attrs.permissions):
        await ws.send_json({
            "type": "error", "request_id": rid,
            "message": "Cannot download a directory",
        })
        return

    name = path.rsplit("/", 1)[-1]
    size = attrs.size or 0

    await ws.send_json({
        "type": "download_start",
        "request_id": rid,
        "name": name,
        "size": size,
    })

    try:
        async with sftp.open(path, "rb") as f:
            while True:
                chunk = await f.read(DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                session.touch()
                await ws.send_bytes(chunk)
    except Exception as exc:
        await ws.send_json({
            "type": "error", "request_id": rid,
            "message": f"Download error: {exc}",
        })
        return

    await ws.send_json({
        "type": "download_end",
        "request_id": rid,
    })


async def _handle_upload_start(
    ws: WebSocket,
    session: SFTPSession,
    sftp: asyncssh.SFTPClient,
    msg: dict,
    rid: str | None,
) -> None:
    """处理上传开始指令."""
    path = msg.get("path", "")
    size = msg.get("size", 0)

    if not path:
        await ws.send_json({
            "type": "error", "request_id": rid,
            "message": "Path is required",
        })
        return

    if size > MAX_UPLOAD_SIZE:
        await ws.send_json({
            "type": "error", "request_id": rid,
            "message": f"File too large (max {MAX_UPLOAD_SIZE // (1024 * 1024)} MB)",
        })
        return

    if session._upload_file is not None:
        await ws.send_json({
            "type": "error", "request_id": rid,
            "message": "Another upload is already in progress",
        })
        return

    try:
        f = await sftp.open(path, "wb")
        if size == 0:
            # 空文件：直接关闭并返回成功
            await f.close()
            await ws.send_json({
                "type": "ok",
                "request_id": rid,
                "message": "Upload complete",
                "path": path,
                "size": 0,
            })
            return
        session._upload_file = f
        session._upload_remaining = size
        session._upload_request_id = rid or ""
        session._upload_received = 0
        session._upload_path = path
        session._upload_last_progress = 0
    except asyncssh.SFTPPermissionDenied:
        await ws.send_json({
            "type": "error", "request_id": rid,
            "message": f"Permission denied: {path}",
        })
    except Exception as exc:
        await ws.send_json({
            "type": "error", "request_id": rid,
            "message": f"Cannot open file for writing: {exc}",
        })


async def _handle_upload_chunk(
    ws: WebSocket,
    session: SFTPSession,
    sftp: asyncssh.SFTPClient,
    data: bytes,
) -> None:
    """处理上传分块数据."""
    if session._upload_file is None:
        # 没有进行中的上传，忽略
        return

    try:
        await session._upload_file.write(data)
        session._upload_received = getattr(session, "_upload_received", 0) + len(data)
        session._upload_remaining -= len(data)

        # 进度推送
        total = session._upload_received + session._upload_remaining
        last_progress = getattr(session, "_upload_last_progress", 0)
        if session._upload_received - last_progress >= UPLOAD_PROGRESS_INTERVAL:
            session._upload_last_progress = session._upload_received
            await ws.send_json({
                "type": "upload_progress",
                "request_id": session._upload_request_id,
                "received": session._upload_received,
                "total": total,
            })

        # 上传完成
        if session._upload_remaining <= 0:
            await session._upload_file.close()
            path = getattr(session, "_upload_path", "")
            await ws.send_json({
                "type": "ok",
                "request_id": session._upload_request_id,
                "message": "Upload complete",
                "path": path,
                "size": session._upload_received,
            })
            session._upload_file = None
            session._upload_remaining = 0
            session._upload_request_id = ""

    except Exception as exc:
        # 上传出错，关闭文件
        try:
            await session._upload_file.close()
        except Exception:
            pass
        session._upload_file = None
        session._upload_remaining = 0
        await ws.send_json({
            "type": "error",
            "request_id": session._upload_request_id,
            "message": f"Upload error: {exc}",
        })
        session._upload_request_id = ""


async def _handle_mkdir(
    ws: WebSocket, sftp: asyncssh.SFTPClient, rid: str | None, path: str,
) -> None:
    """创建目录."""
    if not path:
        await ws.send_json({"type": "error", "request_id": rid, "message": "Path is required"})
        return

    try:
        await sftp.mkdir(path)
    except asyncssh.SFTPPermissionDenied:
        await ws.send_json({
            "type": "error", "request_id": rid,
            "message": f"Permission denied: {path}",
        })
        return
    except asyncssh.SFTPFailure as exc:
        await ws.send_json({
            "type": "error", "request_id": rid,
            "message": f"mkdir failed: {exc}",
        })
        return

    await ws.send_json({
        "type": "ok",
        "request_id": rid,
        "message": f"Directory created: {path}",
    })


async def _handle_rm(
    ws: WebSocket, sftp: asyncssh.SFTPClient, rid: str | None,
    path: str, recursive: bool,
) -> None:
    """删除文件或目录."""
    if not path:
        await ws.send_json({"type": "error", "request_id": rid, "message": "Path is required"})
        return

    try:
        attrs = await sftp.lstat(path)
    except asyncssh.SFTPNoSuchFile:
        await ws.send_json({
            "type": "error", "request_id": rid,
            "message": f"Not found: {path}",
        })
        return

    try:
        if attrs.permissions is not None and stat.S_ISDIR(attrs.permissions):
            if recursive:
                await _rm_recursive(sftp, path)
            else:
                await sftp.rmdir(path)
        else:
            await sftp.remove(path)
    except asyncssh.SFTPPermissionDenied:
        await ws.send_json({
            "type": "error", "request_id": rid,
            "message": f"Permission denied: {path}",
        })
        return
    except asyncssh.SFTPFailure as exc:
        await ws.send_json({
            "type": "error", "request_id": rid,
            "message": f"Delete failed: {exc}",
        })
        return

    await ws.send_json({
        "type": "ok",
        "request_id": rid,
        "message": f"Deleted: {path}",
    })


async def _rm_recursive(sftp: asyncssh.SFTPClient, path: str) -> None:
    """递归删除目录."""
    items = await sftp.readdir(path)
    for item in items:
        if item.filename in (".", ".."):
            continue
        child = f"{path}/{item.filename}"
        if item.attrs.permissions is not None and stat.S_ISDIR(item.attrs.permissions):
            await _rm_recursive(sftp, child)
        else:
            await sftp.remove(child)
    await sftp.rmdir(path)


async def _handle_rename(
    ws: WebSocket, sftp: asyncssh.SFTPClient, rid: str | None,
    old_path: str, new_path: str,
) -> None:
    """重命名/移动文件或目录."""
    if not old_path or not new_path:
        await ws.send_json({
            "type": "error", "request_id": rid,
            "message": "old_path and new_path are required",
        })
        return

    try:
        await sftp.rename(old_path, new_path)
    except asyncssh.SFTPNoSuchFile:
        await ws.send_json({
            "type": "error", "request_id": rid,
            "message": f"Not found: {old_path}",
        })
        return
    except asyncssh.SFTPPermissionDenied:
        await ws.send_json({
            "type": "error", "request_id": rid,
            "message": f"Permission denied",
        })
        return
    except asyncssh.SFTPFailure as exc:
        await ws.send_json({
            "type": "error", "request_id": rid,
            "message": f"Rename failed: {exc}",
        })
        return

    await ws.send_json({
        "type": "ok",
        "request_id": rid,
        "message": f"Renamed: {old_path} → {new_path}",
    })

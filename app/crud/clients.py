"""客户端与节点管理的 CRUD / DAO 操作."""

from __future__ import annotations

import secrets
import time
from typing import Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.clients import (
    Group,
    Server,
    ServerGroup,
    ServerStatus,
)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _generate_token() -> str:
    """生成 Agent 通信认证密钥."""
    return secrets.token_urlsafe(32)


# ═══════════════════════════════════════════════════════════════════════════════
# Server
# ═══════════════════════════════════════════════════════════════════════════════

async def get_server_by_uuid(db: AsyncSession, uuid: str) -> Server | None:
    result = await db.execute(select(Server).where(Server.uuid == uuid))
    return result.scalar_one_or_none()


async def get_server_by_token(db: AsyncSession, token: str) -> Server | None:
    result = await db.execute(select(Server).where(Server.token == token))
    return result.scalar_one_or_none()


async def get_all_servers(
    db: AsyncSession,
    *,
    include_hidden: bool = True,
    include_unapproved: bool = True,
) -> Sequence[Server]:
    """获取服务器列表，支持过滤隐藏和未批准的记录."""
    stmt = select(Server)
    if not include_hidden:
        stmt = stmt.where(Server.hidden == 0)
    if not include_unapproved:
        stmt = stmt.where(Server.is_approved == 1)
    stmt = stmt.order_by(Server.top.desc(), Server.created_at.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


async def create_server(
    db: AsyncSession,
    *,
    name: str,
    remark: str | None = None,
    is_approved: int = 0,
    hardware_info: dict | None = None,
) -> Server:
    """创建服务器记录并自动生成 token.

    Args:
        name: 服务器名称
        remark: 管理员备注
        is_approved: 是否已批准（被动注册默认为 1）
        hardware_info: Agent 上报的硬件信息字典
    """
    token = _generate_token()
    kwargs: dict = dict(
        name=name,
        token=token,
        remark=remark,
        is_approved=is_approved,
    )
    if hardware_info:
        for key in (
            "cpu_name", "virtualization", "arch", "cpu_cores", "os",
            "kernel_version", "ipv4", "ipv6", "mem_total", "swap_total",
            "disk_total", "version",
        ):
            if key in hardware_info and hardware_info[key] is not None:
                kwargs[key] = hardware_info[key]

    server = Server(**kwargs)
    db.add(server)
    await db.flush()

    # 同时创建关联的 server_status 记录
    status = ServerStatus(uuid=server.uuid)
    db.add(status)
    await db.flush()

    return server


async def update_server(db: AsyncSession, uuid: str, **kwargs) -> Server | None:
    """更新服务器字段."""
    await db.execute(update(Server).where(Server.uuid == uuid).values(**kwargs))
    await db.flush()
    return await get_server_by_uuid(db, uuid)


async def update_server_hardware(db: AsyncSession, uuid: str, info: dict) -> None:
    """Agent 上报硬件信息时批量更新."""
    allowed_keys = {
        "cpu_name", "virtualization", "arch", "cpu_cores", "os",
        "kernel_version", "ipv4", "ipv6", "region", "mem_total", "swap_total",
        "disk_total", "version", "name",
    }
    values = {k: v for k, v in info.items() if k in allowed_keys and v is not None}
    if values:
        await db.execute(update(Server).where(Server.uuid == uuid).values(**values))
        await db.flush()


async def delete_server(db: AsyncSession, uuid: str) -> bool:
    """删除服务器及其关联数据（级联删除）."""
    result = await db.execute(delete(Server).where(Server.uuid == uuid))
    return (result.rowcount or 0) > 0


async def approve_server(db: AsyncSession, uuid: str) -> Server | None:
    return await update_server(db, uuid, is_approved=1)


async def regenerate_server_token(db: AsyncSession, uuid: str) -> Server | None:
    """重新生成服务器通信 token."""
    return await update_server(db, uuid, token=_generate_token())


# ═══════════════════════════════════════════════════════════════════════════════
# Server Status
# ═══════════════════════════════════════════════════════════════════════════════

async def get_server_status(db: AsyncSession, uuid: str) -> ServerStatus | None:
    result = await db.execute(
        select(ServerStatus).where(ServerStatus.uuid == uuid))
    return result.scalar_one_or_none()


async def upsert_server_status(
    db: AsyncSession,
    uuid: str,
    *,
    status_val: int | None = None,
    last_online: int | None = None,
    current_run_id: str | None = None,
    boot_time: int | None = None,
) -> ServerStatus:
    """更新或创建服务器状态记录."""
    existing = await get_server_status(db, uuid)
    if existing:
        values: dict = {}
        if status_val is not None:
            values["status"] = status_val
        if last_online is not None:
            values["last_online"] = last_online
        if current_run_id is not None:
            values["current_run_id"] = current_run_id
        if boot_time is not None:
            values["boot_time"] = boot_time
        if values:
            await db.execute(
                update(ServerStatus).where(
                    ServerStatus.uuid == uuid).values(**values)
            )
            await db.flush()
        return (await get_server_status(db, uuid))  # type: ignore[return-value]

    ss = ServerStatus(
        uuid=uuid,
        status=status_val or 0,
        last_online=last_online,
        current_run_id=current_run_id,
        boot_time=boot_time,
    )
    db.add(ss)
    await db.flush()
    return ss


async def get_all_server_statuses(db: AsyncSession) -> Sequence[ServerStatus]:
    result = await db.execute(select(ServerStatus))
    return result.scalars().all()


# ═══════════════════════════════════════════════════════════════════════════════
# Group
# ═══════════════════════════════════════════════════════════════════════════════

async def get_group_by_id(db: AsyncSession, group_id: str) -> Group | None:
    result = await db.execute(select(Group).where(Group.id == group_id))
    return result.scalar_one_or_none()


async def get_group_by_name(db: AsyncSession, name: str) -> Group | None:
    result = await db.execute(select(Group).where(Group.name == name))
    return result.scalar_one_or_none()


async def get_all_groups(db: AsyncSession) -> Sequence[Group]:
    result = await db.execute(
        select(Group).order_by(Group.top.desc().nullslast(), Group.created_at))
    return result.scalars().all()


async def create_group(
    db: AsyncSession,
    *,
    name: str,
    top: int | None = None,
    server_uuids: list[str] | None = None,
) -> Group:
    group = Group(name=name, top=top)
    db.add(group)
    await db.flush()
    if server_uuids:
        for uuid in server_uuids:
            db.add(ServerGroup(server_uuid=uuid, group_id=group.id))
        await db.flush()
    return group


async def update_group(db: AsyncSession, group_id: str, **kwargs) -> Group | None:
    await db.execute(update(Group).where(Group.id == group_id).values(**kwargs))
    await db.flush()
    return await get_group_by_id(db, group_id)


async def delete_group(db: AsyncSession, group_id: str) -> bool:
    result = await db.execute(delete(Group).where(Group.id == group_id))
    return (result.rowcount or 0) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Server ↔ Group
# ═══════════════════════════════════════════════════════════════════════════════

async def get_server_groups(db: AsyncSession, server_uuid: str) -> Sequence[Group]:
    """获取服务器所属的所有分组."""
    result = await db.execute(
        select(Group)
        .join(ServerGroup, Group.id == ServerGroup.group_id)
        .where(ServerGroup.server_uuid == server_uuid)
        .order_by(Group.top.desc().nullslast(), Group.name)
    )
    return result.scalars().all()


async def get_group_servers(db: AsyncSession, group_id: str) -> Sequence[Server]:
    """获取分组下的所有服务器."""
    result = await db.execute(
        select(Server)
        .join(ServerGroup, Server.uuid == ServerGroup.server_uuid)
        .where(ServerGroup.group_id == group_id)
        .order_by(Server.top.desc(), Server.created_at.desc())
    )
    return result.scalars().all()


async def set_server_groups(
    db: AsyncSession,
    server_uuid: str,
    group_ids: list[str],
) -> list[Group]:
    """全量替换服务器所属分组."""
    # 删除原有关联
    await db.execute(
        delete(ServerGroup).where(ServerGroup.server_uuid == server_uuid))
    # 批量插入新关联
    for gid in group_ids:
        db.add(ServerGroup(server_uuid=server_uuid, group_id=gid))
    await db.flush()
    # 返回最新分组列表
    return list(await get_server_groups(db, server_uuid))


async def set_group_servers(
    db: AsyncSession,
    group_id: str,
    server_uuids: list[str],
) -> list[Server]:
    """全量替换分组所属服务器."""
    # 删除原有关联
    await db.execute(
        delete(ServerGroup).where(ServerGroup.group_id == group_id))
    # 批量插入新关联
    for uuid in server_uuids:
        db.add(ServerGroup(server_uuid=uuid, group_id=group_id))
    await db.flush()
    # 返回最新服务器列表
    return list(await get_group_servers(db, group_id))


# ═══════════════════════════════════════════════════════════════════════════════
# Batch Operations
# ═══════════════════════════════════════════════════════════════════════════════

async def batch_update_server_tops(
    db: AsyncSession,
    updates: dict[str, int],
) -> tuple[int, int, list[str]]:
    """批量更新服务器的 top 值.

    Args:
        updates: 格式为 {uuid: top_value} 的字典

    Returns:
        (成功更新数, 失败数, 失败的UUID列表)
    """
    updated_count = 0
    failed_count = 0
    failed_uuids = []

    for uuid_val, top_val in updates.items():
        # 检查服务器是否存在
        server = await get_server_by_uuid(db, uuid_val)
        if not server:
            failed_count += 1
            failed_uuids.append(uuid_val)
            continue

        try:
            await update_server(db, uuid_val, top=top_val)
            updated_count += 1
        except Exception:
            failed_count += 1
            failed_uuids.append(uuid_val)

    await db.commit()
    return updated_count, failed_count, failed_uuids


async def batch_update_group_tops(
    db: AsyncSession,
    updates: dict[str, int],
) -> tuple[int, int, list[str]]:
    """批量更新分组的 top 值.

    Args:
        updates: 格式为 {group_id: top_value} 的字典

    Returns:
        (成功更新数, 失败数, 失败的ID列表)
    """
    updated_count = 0
    failed_count = 0
    failed_ids: list[str] = []

    for group_id, top_val in updates.items():
        group = await get_group_by_id(db, group_id)
        if not group:
            failed_count += 1
            failed_ids.append(group_id)
            continue

        try:
            await update_group(db, group_id, top=top_val)
            updated_count += 1
        except Exception:
            failed_count += 1
            failed_ids.append(group_id)

    await db.commit()
    return updated_count, failed_count, failed_ids

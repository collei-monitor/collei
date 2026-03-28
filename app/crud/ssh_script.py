"""SSH 快捷脚本库的 CRUD / DAO 操作."""

from __future__ import annotations

import time
from typing import Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ssh_script import SshScript


# ═══════════════════════════════════════════════════════════════════════════════
# SshScript
# ═══════════════════════════════════════════════════════════════════════════════

async def get_ssh_script(db: AsyncSession, script_id: int) -> SshScript | None:
    """根据 ID 获取单条脚本."""
    result = await db.execute(
        select(SshScript).where(SshScript.id == script_id),
    )
    return result.scalar_one_or_none()


async def get_all_ssh_scripts(db: AsyncSession) -> Sequence[SshScript]:
    """获取所有脚本（按 top 降序、创建时间降序）."""
    result = await db.execute(
        select(SshScript).order_by(
            SshScript.top.desc(),
            SshScript.created_at.desc(),
        ),
    )
    return result.scalars().all()


async def create_ssh_script(
    db: AsyncSession,
    *,
    name: str,
    content: str,
    description: str | None = None,
    language: str = "bash",
) -> SshScript:
    """创建一条脚本记录."""
    script = SshScript(
        name=name,
        content=content,
        description=description,
        language=language,
    )
    db.add(script)
    await db.flush()
    return script


async def update_ssh_script(
    db: AsyncSession,
    script_id: int,
    **kwargs,
) -> SshScript | None:
    """更新脚本字段（自动刷新 updated_at）."""
    kwargs["updated_at"] = int(time.time())
    await db.execute(
        update(SshScript)
        .where(SshScript.id == script_id)
        .values(**kwargs),
    )
    await db.flush()
    return await get_ssh_script(db, script_id)


async def delete_ssh_script(db: AsyncSession, script_id: int) -> bool:
    """删除脚本."""
    result = await db.execute(
        delete(SshScript).where(SshScript.id == script_id),
    )
    return (result.rowcount or 0) > 0


async def batch_update_tops(
    db: AsyncSession,
    updates: dict[int, int],
) -> tuple[int, int, list[int]]:
    """批量更新脚本排序值.

    Returns:
        (updated_count, failed_count, failed_ids)
    """
    now = int(time.time())
    updated = 0
    failed_ids: list[int] = []

    for script_id, top_val in updates.items():
        result = await db.execute(
            update(SshScript)
            .where(SshScript.id == script_id)
            .values(top=top_val, updated_at=now),
        )
        if result.rowcount:
            updated += 1
        else:
            failed_ids.append(script_id)

    await db.flush()
    return updated, len(failed_ids), failed_ids

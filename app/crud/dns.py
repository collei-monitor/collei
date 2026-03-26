"""DNS 与 DDNS 的 CRUD / DAO 操作."""

from __future__ import annotations

import json
import time
from typing import Any, Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.crypto import decrypt_credential, encrypt_credential
from app.models.dns import DdnsTask, DnsCredential, DnsDomain, DnsRecord


# ═══════════════════════════════════════════════════════════════════════════════
# DnsCredential — 凭证管理
# ═══════════════════════════════════════════════════════════════════════════════

async def create_credential(
    db: AsyncSession,
    *,
    name: str,
    provider: str,
    credentials: dict,
) -> DnsCredential:
    """创建 DNS 凭证（自动加密 credentials）."""
    cred = DnsCredential(
        name=name,
        provider=provider,
        credentials=encrypt_credential(json.dumps(credentials)),
    )
    db.add(cred)
    await db.flush()
    return cred


async def get_credential(db: AsyncSession, cred_id: int) -> DnsCredential | None:
    result = await db.execute(
        select(DnsCredential).where(DnsCredential.id == cred_id),
    )
    return result.scalar_one_or_none()


async def get_all_credentials(db: AsyncSession) -> Sequence[DnsCredential]:
    result = await db.execute(
        select(DnsCredential).order_by(DnsCredential.id.asc()),
    )
    return result.scalars().all()


async def update_credential(
    db: AsyncSession,
    cred_id: int,
    **kwargs: Any,
) -> DnsCredential | None:
    """更新凭证字段（credentials 字典自动加密）."""
    allowed = {"name", "credentials", "is_valid"}
    values: dict[str, Any] = {}
    for k, v in kwargs.items():
        if k not in allowed or v is None:
            continue
        if k == "credentials" and isinstance(v, dict):
            values[k] = encrypt_credential(json.dumps(v))
        else:
            values[k] = v
    if not values:
        return await get_credential(db, cred_id)
    values["updated_at"] = int(time.time())
    await db.execute(
        update(DnsCredential).where(DnsCredential.id == cred_id).values(**values),
    )
    await db.flush()
    return await get_credential(db, cred_id)


async def delete_credential(db: AsyncSession, cred_id: int) -> bool:
    result = await db.execute(
        delete(DnsCredential).where(DnsCredential.id == cred_id),
    )
    await db.flush()
    return (result.rowcount or 0) > 0


def get_decrypted_credentials(cred: DnsCredential) -> dict:
    """解密凭证 JSON blob."""
    return json.loads(decrypt_credential(cred.credentials))


async def mark_credential_invalid(db: AsyncSession, cred_id: int) -> None:
    """将凭证标记为无效（API 调用返回 401/403 时）."""
    await db.execute(
        update(DnsCredential)
        .where(DnsCredential.id == cred_id)
        .values(is_valid=0, updated_at=int(time.time())),
    )
    await db.flush()


# ═══════════════════════════════════════════════════════════════════════════════
# DnsDomain — 域名管理
# ═══════════════════════════════════════════════════════════════════════════════

async def create_domain(
    db: AsyncSession,
    *,
    credential_id: int,
    domain_name: str,
    zone_id: str | None = None,
) -> DnsDomain:
    domain = DnsDomain(
        credential_id=credential_id,
        domain_name=domain_name,
        zone_id=zone_id,
    )
    db.add(domain)
    await db.flush()
    return domain


async def get_domain(db: AsyncSession, domain_id: int) -> DnsDomain | None:
    result = await db.execute(
        select(DnsDomain).where(DnsDomain.id == domain_id),
    )
    return result.scalar_one_or_none()


async def get_domain_with_credential(
    db: AsyncSession, domain_id: int,
) -> DnsDomain | None:
    """获取域名并预加载关联的凭证."""
    result = await db.execute(
        select(DnsDomain)
        .options(selectinload(DnsDomain.credential))
        .where(DnsDomain.id == domain_id),
    )
    return result.scalar_one_or_none()


async def get_all_domains(db: AsyncSession) -> Sequence[DnsDomain]:
    result = await db.execute(
        select(DnsDomain).order_by(DnsDomain.id.asc()),
    )
    return result.scalars().all()


async def update_domain(
    db: AsyncSession,
    domain_id: int,
    **kwargs: Any,
) -> DnsDomain | None:
    allowed = {"credential_id", "zone_id", "sync_status", "last_sync_at"}
    values = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not values:
        return await get_domain(db, domain_id)
    values["updated_at"] = int(time.time())
    await db.execute(
        update(DnsDomain).where(DnsDomain.id == domain_id).values(**values),
    )
    await db.flush()
    return await get_domain(db, domain_id)


async def delete_domain(db: AsyncSession, domain_id: int) -> bool:
    result = await db.execute(
        delete(DnsDomain).where(DnsDomain.id == domain_id),
    )
    await db.flush()
    return (result.rowcount or 0) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# DnsRecord — 解析记录
# ═══════════════════════════════════════════════════════════════════════════════

async def create_record(
    db: AsyncSession,
    *,
    domain_id: int,
    record_id: str,
    name: str,
    type: str,
    content: str,
    ttl: int = 600,
    priority: int | None = None,
    proxied: int = 0,
    status: str = "active",
) -> DnsRecord:
    rec = DnsRecord(
        domain_id=domain_id,
        record_id=record_id,
        name=name,
        type=type,
        content=content,
        ttl=ttl,
        priority=priority,
        proxied=proxied,
        status=status,
        synced_at=int(time.time()),
    )
    db.add(rec)
    await db.flush()
    return rec


async def get_record(db: AsyncSession, rec_id: int) -> DnsRecord | None:
    result = await db.execute(
        select(DnsRecord).where(DnsRecord.id == rec_id),
    )
    return result.scalar_one_or_none()


async def get_records_by_domain(
    db: AsyncSession, domain_id: int,
) -> Sequence[DnsRecord]:
    result = await db.execute(
        select(DnsRecord)
        .where(DnsRecord.domain_id == domain_id)
        .order_by(DnsRecord.name.asc(), DnsRecord.type.asc()),
    )
    return result.scalars().all()


async def update_record(
    db: AsyncSession,
    rec_id: int,
    **kwargs: Any,
) -> DnsRecord | None:
    allowed = {"content", "ttl", "priority", "proxied", "status", "synced_at", "record_id"}
    values = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not values:
        return await get_record(db, rec_id)
    await db.execute(
        update(DnsRecord).where(DnsRecord.id == rec_id).values(**values),
    )
    await db.flush()
    return await get_record(db, rec_id)


async def delete_record(db: AsyncSession, rec_id: int) -> bool:
    result = await db.execute(
        delete(DnsRecord).where(DnsRecord.id == rec_id),
    )
    await db.flush()
    return (result.rowcount or 0) > 0


async def bulk_upsert_records(
    db: AsyncSession,
    domain_id: int,
    remote_records: list[dict],
) -> int:
    """从厂商同步的记录批量 upsert 到本地缓存.

    remote_records 每项需包含: record_id, name, type, content, ttl
    可选: priority, proxied, status

    返回更新/新增的总数.
    """
    now = int(time.time())
    # 加载当前域名下所有本地记录
    existing_result = await db.execute(
        select(DnsRecord).where(DnsRecord.domain_id == domain_id),
    )
    existing_map: dict[str, DnsRecord] = {
        r.record_id: r for r in existing_result.scalars().all()
    }

    count = 0
    seen_ids: set[str] = set()
    for rd in remote_records:
        rid = str(rd["record_id"])
        seen_ids.add(rid)
        if rid in existing_map:
            # 更新现有记录
            await db.execute(
                update(DnsRecord)
                .where(DnsRecord.id == existing_map[rid].id)
                .values(
                    name=rd.get("name", existing_map[rid].name),
                    type=rd.get("type", existing_map[rid].type),
                    content=rd.get("content", existing_map[rid].content),
                    ttl=rd.get("ttl", existing_map[rid].ttl),
                    priority=rd.get("priority"),
                    proxied=rd.get("proxied", 0),
                    status=rd.get("status", "active"),
                    synced_at=now,
                ),
            )
        else:
            # 新增记录
            db.add(DnsRecord(
                domain_id=domain_id,
                record_id=rid,
                name=rd["name"],
                type=rd["type"],
                content=rd["content"],
                ttl=rd.get("ttl", 600),
                priority=rd.get("priority"),
                proxied=rd.get("proxied", 0),
                status=rd.get("status", "active"),
                synced_at=now,
            ))
        count += 1

    # 删除厂商侧已不存在的本地记录
    stale_ids = [
        existing_map[rid].id
        for rid in existing_map
        if rid not in seen_ids
    ]
    if stale_ids:
        await db.execute(
            delete(DnsRecord).where(DnsRecord.id.in_(stale_ids)),
        )

    await db.flush()
    return count


# ═══════════════════════════════════════════════════════════════════════════════
# DdnsTask — DDNS 任务
# ═══════════════════════════════════════════════════════════════════════════════

async def create_ddns_task(
    db: AsyncSession,
    *,
    record_id: int,
    server_uuid: str,
    ip_version: str = "ipv4",
) -> DdnsTask:
    task = DdnsTask(
        record_id=record_id,
        server_uuid=server_uuid,
        ip_version=ip_version,
    )
    db.add(task)
    await db.flush()
    result = await get_ddns_task(db, task.id)
    assert result is not None
    return result


async def get_ddns_task(db: AsyncSession, task_id: int) -> DdnsTask | None:
    result = await db.execute(
        select(DdnsTask)
        .options(selectinload(DdnsTask.record).selectinload(DnsRecord.domain))
        .where(DdnsTask.id == task_id),
    )
    return result.scalar_one_or_none()


async def get_all_ddns_tasks(
    db: AsyncSession, *, active_only: bool = False,
) -> Sequence[DdnsTask]:
    stmt = (
        select(DdnsTask)
        .options(selectinload(DdnsTask.record).selectinload(DnsRecord.domain))
        .order_by(DdnsTask.id.asc())
    )
    if active_only:
        stmt = stmt.where(DdnsTask.is_active == 1)
    result = await db.execute(stmt)
    return result.scalars().all()


async def update_ddns_task(
    db: AsyncSession,
    task_id: int,
    **kwargs: Any,
) -> DdnsTask | None:
    allowed = {
        "server_uuid", "ip_version", "is_active",
        "last_ip", "last_updated", "last_error", "error_count",
    }
    values = {k: v for k, v in kwargs.items() if k in allowed}
    # 过滤 None，但允许显式设为 None 的字段（如 last_error）
    if not values:
        return await get_ddns_task(db, task_id)
    await db.execute(
        update(DdnsTask).where(DdnsTask.id == task_id).values(**values),
    )
    await db.flush()
    return await get_ddns_task(db, task_id)


async def delete_ddns_task(db: AsyncSession, task_id: int) -> bool:
    result = await db.execute(
        delete(DdnsTask).where(DdnsTask.id == task_id),
    )
    await db.flush()
    return (result.rowcount or 0) > 0

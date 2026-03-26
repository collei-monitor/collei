"""DNS 解析记录 API 路由（需管理员登录）.

所有写操作（创建/更新/删除）会先推送到 DNS 厂商，成功后再更新本地缓存。

端点:
  GET     /dns/domains/{domain_id}/records           获取域名下所有记录
  POST    /dns/domains/{domain_id}/records           创建记录
  PUT     /dns/records/{id}                          更新记录
  DELETE  /dns/records/{id}                          删除记录
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core import dns_service
from app.core.dns_service import DnsAuthError, DnsServiceError
from app.crud import dns as crud
from app.db.session import get_async_session
from app.models.auth import User
from app.models.dns import DnsCredential, DnsDomain
from app.schemas.dns import (
    MessageResponse,
    RecordCreate,
    RecordRead,
    RecordUpdate,
)

router = APIRouter()


# ─── 辅助 ────────────────────────────────────────────────────────────────────

async def _resolve_domain_and_auth(
    db, domain_id: int,
) -> tuple[DnsDomain, DnsCredential, dict]:
    """获取域名、凭证对象及解密后的凭证信息，失败时抛出 HTTPException."""
    domain = await crud.get_domain_with_credential(db, domain_id)
    if not domain:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found")
    if not domain.credential:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Domain has no credential bound")
    credential = domain.credential
    auth = crud.get_decrypted_credentials(credential)
    return domain, credential, auth


def _handle_dns_error(e: Exception, cred_id: int | None = None):
    """将 DnsServiceError 转为 HTTPException."""
    if isinstance(e, DnsAuthError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="DNS credential authentication failed")
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"DNS provider error: {e}")


# ─── 端点 ────────────────────────────────────────────────────────────────────

@router.get(
    "/domains/{domain_id}/records",
    response_model=list[RecordRead],
)
async def list_records(
    domain_id: int,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取域名下所有本地缓存的解析记录."""
    domain = await crud.get_domain(db, domain_id)
    if not domain:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found")
    return await crud.get_records_by_domain(db, domain_id)


@router.post(
    "/domains/{domain_id}/records",
    response_model=RecordRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_record(
    domain_id: int,
    body: RecordCreate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """创建解析记录 — 先推送到厂商，成功后写入本地缓存."""
    domain, credential, auth = await _resolve_domain_and_auth(db, domain_id)

    try:
        await dns_service.create_record(
            provider=credential.provider,
            domain=domain.domain_name,
            auth_params=auth,
            rtype=body.type,
            name=body.name,
            content=body.content,
            zone_id=domain.zone_id,
        )
    except DnsAuthError:
        await crud.mark_credential_invalid(db, credential.id)
        _handle_dns_error(DnsAuthError("auth failed"))
    except DnsServiceError as e:
        _handle_dns_error(e)

    # 厂商成功 → 从厂商拉取最新记录获取 record_id
    # lexicon create_record 不返回 ID，需重新 list 获取
    try:
        remote = await dns_service.list_records(
            provider=credential.provider,
            domain=domain.domain_name,
            auth_params=auth,
            zone_id=domain.zone_id,
            rtype=body.type,
            name=body.name,
        )
    except DnsServiceError:
        remote = []

    # 尝试匹配刚创建的记录
    record_id = ""
    for r in remote:
        if (r.get("content", "") == body.content
                and r.get("type", "") == body.type):
            record_id = str(r.get("id", r.get("record_id", "")))
            break

    return await crud.create_record(
        db,
        domain_id=domain_id,
        record_id=record_id,
        name=body.name,
        type=body.type,
        content=body.content,
        ttl=body.ttl,
        priority=body.priority,
        proxied=body.proxied,
    )


@router.put("/records/{rec_id}", response_model=RecordRead)
async def update_record(
    rec_id: int,
    body: RecordUpdate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """更新解析记录 — 先推送到厂商，成功后更新本地缓存."""
    rec = await crud.get_record(db, rec_id)
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")

    domain, credential, auth = await _resolve_domain_and_auth(db, rec.domain_id)

    new_content = body.content if body.content is not None else rec.content
    try:
        await dns_service.update_record(
            provider=credential.provider,
            domain=domain.domain_name,
            auth_params=auth,
            identifier=rec.record_id,
            rtype=rec.type,
            name=rec.name,
            content=new_content,
            zone_id=domain.zone_id,
        )
    except DnsAuthError:
        await crud.mark_credential_invalid(db, credential.id)
        _handle_dns_error(DnsAuthError("auth failed"))
    except DnsServiceError as e:
        _handle_dns_error(e)

    update_fields = body.model_dump(exclude_unset=True)
    update_fields["synced_at"] = int(time.time())
    return await crud.update_record(db, rec_id, **update_fields)


@router.delete("/records/{rec_id}", response_model=MessageResponse)
async def delete_record(
    rec_id: int,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """删除解析记录 — 先从厂商删除，成功后删除本地缓存."""
    rec = await crud.get_record(db, rec_id)
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")

    domain, credential, auth = await _resolve_domain_and_auth(db, rec.domain_id)

    try:
        await dns_service.delete_record(
            provider=credential.provider,
            domain=domain.domain_name,
            auth_params=auth,
            identifier=rec.record_id,
            zone_id=domain.zone_id,
            rtype=rec.type,
            name=rec.name,
            content=rec.content,
        )
    except DnsAuthError:
        await crud.mark_credential_invalid(db, credential.id)
        _handle_dns_error(DnsAuthError("auth failed"))
    except DnsServiceError as e:
        _handle_dns_error(e)

    await crud.delete_record(db, rec_id)
    return MessageResponse(message="Record deleted")

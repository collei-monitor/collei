"""DNS 域名 API 路由（需管理员登录）.

端点:
  POST    /dns/domains                  添加域名
  GET     /dns/domains                  获取所有域名
  GET     /dns/domains/{id}             获取单个域名
  PUT     /dns/domains/{id}             更新域名
  DELETE  /dns/domains/{id}             删除域名
  POST    /dns/domains/{id}/sync        从厂商同步记录到本地
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core import dns_service
from app.core.dns_service import DnsAuthError, DnsServiceError
from app.crud import dns as crud
from app.db.session import get_async_session
from app.models.auth import User
from app.schemas.dns import (
    DomainCreate,
    DomainRead,
    DomainUpdate,
    MessageResponse,
)

router = APIRouter()
logger = logging.getLogger("collei.dns.domains")


@router.post(
    "/domains",
    response_model=DomainRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_domain(
    body: DomainCreate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """添加托管域名."""
    cred = await crud.get_credential(db, body.credential_id)
    if not cred:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")
    return await crud.create_domain(
        db,
        credential_id=body.credential_id,
        domain_name=body.domain_name,
        zone_id=body.zone_id,
    )


@router.get("/domains", response_model=list[DomainRead])
async def list_domains(
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取所有域名."""
    return await crud.get_all_domains(db)


@router.get("/domains/{domain_id}", response_model=DomainRead)
async def get_domain(
    domain_id: int,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取单个域名."""
    domain = await crud.get_domain(db, domain_id)
    if not domain:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found")
    return domain


@router.put("/domains/{domain_id}", response_model=DomainRead)
async def update_domain(
    domain_id: int,
    body: DomainUpdate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """更新域名信息."""
    existing = await crud.get_domain(db, domain_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found")
    return await crud.update_domain(db, domain_id, **body.model_dump(exclude_unset=True))


@router.delete("/domains/{domain_id}", response_model=MessageResponse)
async def delete_domain(
    domain_id: int,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """删除域名及其下所有解析记录和 DDNS 任务（级联删除）."""
    deleted = await crud.delete_domain(db, domain_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found")
    return MessageResponse(message="Domain deleted")


@router.post("/domains/{domain_id}/sync", response_model=MessageResponse)
async def sync_domain_records(
    domain_id: int,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """从厂商同步域名的所有解析记录到本地缓存."""
    domain = await crud.get_domain_with_credential(db, domain_id)
    if not domain:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found")
    if not domain.credential:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Domain has no credential bound")

    auth_params = crud.get_decrypted_credentials(domain.credential)
    try:
        remote = await dns_service.list_records(
            provider=domain.credential.provider,
            domain=domain.domain_name,
            auth_params=auth_params,
            zone_id=domain.zone_id,
        )
    except DnsAuthError:
        await crud.mark_credential_invalid(db, domain.credential.id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="DNS credential authentication failed")
    except DnsServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"DNS provider error: {e}")

    # 将远端记录格式化为 bulk_upsert 需要的字典列表
    formatted = []
    for r in remote:
        formatted.append({
            "record_id": str(r.get("id", r.get("record_id", ""))),
            "name": r.get("name", ""),
            "type": r.get("type", "A"),
            "content": r.get("content", ""),
            "ttl": r.get("ttl", 600),
            "priority": r.get("priority"),
            "proxied": r.get("proxied", 0),
            "status": r.get("status", "active"),
        })

    count = await crud.bulk_upsert_records(db, domain_id, formatted)
    await crud.update_domain(
        db, domain_id, sync_status="synced", last_sync_at=int(time.time()))
    logger.info("Synced %d records for domain %s", count, domain.domain_name)
    return MessageResponse(message=f"Synced {count} records")

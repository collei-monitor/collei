"""DNS 凭证 API 路由（需管理员登录）.

端点:
  POST    /dns/credentials              创建凭证
  GET     /dns/credentials              获取所有凭证
  GET     /dns/credentials/{id}         获取单个凭证
  PUT     /dns/credentials/{id}         更新凭证
  DELETE  /dns/credentials/{id}         删除凭证
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.crud import dns as crud
from app.db.session import get_async_session
from app.models.auth import User
from app.schemas.dns import (
    CredentialCreate,
    CredentialRead,
    CredentialUpdate,
    MessageResponse,
)

router = APIRouter()


@router.post(
    "/credentials",
    response_model=CredentialRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_credential(
    body: CredentialCreate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """创建 DNS 凭证."""
    return await crud.create_credential(
        db,
        name=body.name,
        provider=body.provider,
        credentials=body.credentials,
    )


@router.get("/credentials", response_model=list[CredentialRead])
async def list_credentials(
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取所有 DNS 凭证."""
    return await crud.get_all_credentials(db)


@router.get("/credentials/{cred_id}", response_model=CredentialRead)
async def get_credential(
    cred_id: int,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """获取单个 DNS 凭证."""
    cred = await crud.get_credential(db, cred_id)
    if not cred:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")
    return cred


@router.put("/credentials/{cred_id}", response_model=CredentialRead)
async def update_credential(
    cred_id: int,
    body: CredentialUpdate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """更新 DNS 凭证."""
    existing = await crud.get_credential(db, cred_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")
    updated = await crud.update_credential(
        db, cred_id, **body.model_dump(exclude_unset=True))
    return updated


@router.delete("/credentials/{cred_id}", response_model=MessageResponse)
async def delete_credential(
    cred_id: int,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """删除 DNS 凭证（关联域名的 credential_id 将置 NULL）."""
    deleted = await crud.delete_credential(db, cred_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")
    return MessageResponse(message="Credential deleted")

"""API Keys endpoint - CRUD 和程序化访问."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import CurrentUser
from app.db.session import get_db
from app.models import Organization, User
from app.services.api_keys import (
    create_api_key,
    list_api_keys,
    revoke_api_key,
)

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/api-keys", tags=["api-keys"])


# ============ Schemas ============

class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ApiKeyResponse(BaseModel):
    id: int
    name: str
    key_prefix: str
    last_used_at: str | None
    revoked: bool
    created_at: str
    model_config = ConfigDict(from_attributes=True)


class ApiKeyWithSecretResponse(ApiKeyResponse):
    """创建时返回明文, 仅此一次."""
    key: str


# ============ Helpers ============

async def _ensure_user_has_org(user: User, db: AsyncSession) -> Organization:
    if user.organization_id:
        org = await db.get(Organization, user.organization_id)
        if org:
            return org
    from app.core.plans import get_plan_limits
    limits = get_plan_limits("free")
    org = Organization(
        name=f"Org of {user.email}",
        plan="free",
        credits_balance=limits.monthly_credits,
        monthly_credits=limits.monthly_credits,
    )
    db.add(org)
    await db.commit()
    await db.refresh(org)
    user.organization_id = org.id
    await db.commit()
    return org


# ============ Endpoints ============

@router.get("", response_model=list[ApiKeyResponse])
async def list_keys(
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[ApiKeyResponse]:
    """列出当前组织的 API keys."""
    org = await _ensure_user_has_org(user, db)
    keys = await list_api_keys(db, org.id)
    return [
        ApiKeyResponse(
            id=k.id,
            name=k.name,
            key_prefix=k.key_prefix,
            last_used_at=k.last_used_at.isoformat() if k.last_used_at else None,
            revoked=bool(k.revoked),
            created_at=k.created_at.isoformat(),
        )
        for k in keys
    ]


@router.post("", response_model=ApiKeyWithSecretResponse, status_code=status.HTTP_201_CREATED)
async def create_key(
    req: ApiKeyCreateRequest,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> ApiKeyWithSecretResponse:
    """创建新的 API key. 明文 key 仅返回一次."""
    org = await _ensure_user_has_org(user, db)
    api_key, plaintext = await create_api_key(db, user, org, req.name)

    # 审计
    from app.services.audit import record_audit
    await record_audit(
        db,
        org_id=org.id,
        user_id=user.id,
        action="api_key.created",
        resource_type="api_key",
        resource_id=str(api_key.id),
        payload={"name": req.name},
    )

    return ApiKeyWithSecretResponse(
        id=api_key.id,
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        last_used_at=None,
        revoked=False,
        created_at=api_key.created_at.isoformat(),
        key=plaintext,
    )


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(
    key_id: int,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """撤销 API key."""
    org = await _ensure_user_has_org(user, db)
    success = await revoke_api_key(db, key_id, org.id)
    if not success:
        raise HTTPException(status_code=404, detail="API key not found")

    from app.services.audit import record_audit
    await record_audit(
        db,
        org_id=org.id,
        user_id=user.id,
        action="api_key.revoked",
        resource_type="api_key",
        resource_id=str(key_id),
    )

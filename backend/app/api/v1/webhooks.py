"""Webhooks endpoint - 用户配置外部 webhook."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import CurrentUser
from app.db.session import get_db
from app.models import Organization, User
from app.services.audit import record_audit
from app.services.webhooks import (
    create_webhook,
    delete_webhook,
    list_webhooks,
)

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])

# 支持的事件类型
SUPPORTED_EVENTS = [
    "project.created",
    "project.completed",
    "project.failed",
    "subscription.updated",
    "subscription.canceled",
    "member.invited",
    "member.removed",
]


# ============ Schemas ============

class WebhookCreateRequest(BaseModel):
    url: str = Field(min_length=10, max_length=1024)
    events: list[str] = Field(min_length=1)


class WebhookResponse(BaseModel):
    id: int
    url: str
    events: list[str]
    status: str
    last_triggered_at: str | None
    last_response_status: int | None
    created_at: str
    model_config = ConfigDict(from_attributes=True)


class WebhookWithSecretResponse(WebhookResponse):
    """创建时返回 secret, 仅此一次."""
    secret: str


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

@router.get("/events")
async def list_supported_events() -> dict:
    """列出支持订阅的事件类型."""
    return {"events": SUPPORTED_EVENTS}


@router.get("", response_model=list[WebhookResponse])
async def list_webhook_endpoints(
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[WebhookResponse]:
    org = await _ensure_user_has_org(user, db)
    endpoints = await list_webhooks(db, org.id)
    return [
        WebhookResponse(
            id=e.id,
            url=e.url,
            events=e.events or [],
            status=e.status,
            last_triggered_at=e.last_triggered_at.isoformat() if e.last_triggered_at else None,
            last_response_status=e.last_response_status,
            created_at=e.created_at.isoformat(),
        )
        for e in endpoints
    ]


@router.post("", response_model=WebhookWithSecretResponse, status_code=status.HTTP_201_CREATED)
async def create_webhook_endpoint(
    req: WebhookCreateRequest,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> WebhookWithSecretResponse:
    """创建 webhook endpoint. secret 仅返回一次."""
    # 校验 events
    invalid = [e for e in req.events if e not in SUPPORTED_EVENTS]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported events: {invalid}. valid: {SUPPORTED_EVENTS}",
        )

    if not req.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must start with http:// or https://")

    org = await _ensure_user_has_org(user, db)
    endpoint, secret = await create_webhook(db, org.id, req.url, req.events)

    await record_audit(
        db,
        org_id=org.id,
        user_id=user.id,
        action="webhook.created",
        resource_type="webhook",
        resource_id=str(endpoint.id),
        payload={"url": req.url, "events": req.events},
    )

    return WebhookWithSecretResponse(
        id=endpoint.id,
        url=endpoint.url,
        events=endpoint.events or [],
        status=endpoint.status,
        last_triggered_at=None,
        last_response_status=None,
        created_at=endpoint.created_at.isoformat(),
        secret=secret,
    )


@router.delete("/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook_endpoint(
    endpoint_id: int,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    org = await _ensure_user_has_org(user, db)
    success = await delete_webhook(db, endpoint_id, org.id)
    if not success:
        raise HTTPException(status_code=404, detail="webhook not found")

    await record_audit(
        db,
        org_id=org.id,
        user_id=user.id,
        action="webhook.deleted",
        resource_type="webhook",
        resource_id=str(endpoint_id),
    )

"""OPC Billing API - 用量查询、plans 列表、额度余额、订阅管理.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger
from app.core.plans import PLAN_LIMITS, PlanLimits, get_plan_limits
from app.core.security import CurrentUser
from app.db.session import get_db, async_session_factory
from app.models import Organization, User
from app.services.billing import get_monthly_usage
from app.services import stripe_service

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])
webhook_router = APIRouter(prefix="/api/v1/billing", tags=["billing-webhook"])


# ============ Schemas ============

class PlanInfo(BaseModel):
    plan: str
    monthly_credits: int
    max_projects_per_month: int
    max_team_members: int
    has_private_projects: bool
    has_api_access: bool
    has_sso: bool
    priority_support: bool
    price_monthly: int  # USD cents, 0 means free
    price_yearly: int  # USD cents


class UsageResponse(BaseModel):
    organization_id: int
    plan: str
    credits_balance: int
    monthly_credits: int
    current_period_usage: dict
    model_config = ConfigDict(from_attributes=True)


class CheckoutRequest(BaseModel):
    plan: str
    period: str  # monthly / yearly


class CheckoutResponse(BaseModel):
    url: str
    session_id: str


class PortalResponse(BaseModel):
    url: str


class StripeStatusResponse(BaseModel):
    configured: bool


# ============ 价格表 (USD cents) ============

PLAN_PRICES = {
    "free": (0, 0),
    "lite": (1900, 19000),  # $19/mo, $190/yr
    "pro": (4900, 49000),  # $49/mo, $490/yr
    "max": (12900, 129000),  # $129/mo, $1290/yr
    "enterprise": (0, 0),  # custom
}


def _plan_to_info(plan: str) -> PlanInfo:
    limits: PlanLimits = get_plan_limits(plan)
    monthly, yearly = PLAN_PRICES.get(plan, (0, 0))
    return PlanInfo(
        plan=limits.plan,
        monthly_credits=limits.monthly_credits,
        max_projects_per_month=limits.max_projects_per_month,
        max_team_members=limits.max_team_members,
        has_private_projects=limits.has_private_projects,
        has_api_access=limits.has_api_access,
        has_sso=limits.has_sso,
        priority_support=limits.priority_support,
        price_monthly=monthly,
        price_yearly=yearly,
    )


async def _ensure_user_has_org(user: User, db: AsyncSession) -> int:
    if user.organization_id:
        return user.organization_id
    from app.models import Organization
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
    return org.id


# ============ Endpoints ============

@router.get("/plans", response_model=list[PlanInfo])
async def list_plans() -> list[PlanInfo]:
    """列出所有订阅 plan 配置. 公开端点, 无需认证."""
    return [_plan_to_info(p) for p in PLAN_LIMITS.keys() if p != "enterprise"] + [
        _plan_to_info("enterprise")
    ]


@router.get("/usage", response_model=UsageResponse)
async def get_usage(
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> UsageResponse:
    """获取当前用户组织的用量统计."""
    org_id = await _ensure_user_has_org(user, db)
    org = await db.get(Organization, org_id)  # type: ignore[arg-type]
    if org is None:
        raise HTTPException(status_code=500, detail="organization not found")

    usage = await get_monthly_usage(db, org)
    limits = get_plan_limits(org.plan)

    return UsageResponse(
        organization_id=org.id,
        plan=org.plan,
        credits_balance=org.credits_balance,
        monthly_credits=limits.monthly_credits,
        current_period_usage=usage,
    )


@router.get("/stripe/status", response_model=StripeStatusResponse)
async def get_stripe_status() -> StripeStatusResponse:
    """检查 Stripe 是否已配置. 公开端点."""
    return StripeStatusResponse(configured=bool(settings.stripe_secret_key))


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    req: CheckoutRequest,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> CheckoutResponse:
    """创建 Stripe Checkout Session, 返回 URL 供前端跳转."""
    if not settings.stripe_secret_key:
        raise HTTPException(
            status_code=503,
            detail="Stripe is not configured. Set STRIPE_SECRET_KEY.",
        )

    org_id = await _ensure_user_has_org(user, db)
    org = await db.get(Organization, org_id)  # type: ignore[arg-type]
    if org is None:
        raise HTTPException(status_code=500, detail="organization not found")

    try:
        url = await stripe_service.create_checkout_session(db, user, org, req.plan, req.period)
        # 提取 session_id (URL 末段)
        session_id = url.rsplit("/", 1)[-1].split("?")[0]
        return CheckoutResponse(url=url, session_id=session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/portal", response_model=PortalResponse)
async def create_portal(
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> PortalResponse:
    """创建 Stripe Customer Portal session, 让用户管理订阅."""
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Stripe is not configured")

    if not user.organization_id:
        raise HTTPException(status_code=400, detail="user has no organization")

    org = await db.get(Organization, user.organization_id)  # type: ignore[arg-type]
    if org is None:
        raise HTTPException(status_code=500, detail="organization not found")

    try:
        url = await stripe_service.create_customer_portal_session(db, org)
        return PortalResponse(url=url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


# ============ Webhook endpoint (no auth, signature verified) ============

@webhook_router.post("/webhook", response_class=PlainTextResponse)
async def stripe_webhook(request: Request) -> PlainTextResponse:
    """Stripe webhook 入口.

    签名验证在 stripe_service.handle_webhook_event 中完成.
    """
    payload = await request.body()
    signature = request.headers.get("Stripe-Signature", "")

    if not settings.stripe_secret_key:
        return PlainTextResponse("Stripe not configured", status_code=503)
    if not settings.stripe_webhook_secret:
        return PlainTextResponse("Webhook secret not configured", status_code=503)

    # 用独立 session 避免与请求级 session 冲突
    async with async_session_factory() as session:
        try:
            await stripe_service.handle_webhook_event(session, payload, signature)
            await session.commit()
        except ValueError as e:
            log.warning("stripe_webhook_signature_invalid", error=str(e))
            return PlainTextResponse("Invalid signature", status_code=400)
        except Exception as e:
            log.error("stripe_webhook_failed", error=str(e), error_type=type(e).__name__)
            await session.rollback()
            return PlainTextResponse("Webhook failed", status_code=500)

    return PlainTextResponse("ok", status_code=200)

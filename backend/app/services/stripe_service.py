"""Stripe service - 创建 checkout session、处理 webhook、同步订阅状态.

设计:
1. checkout(plan, period) → 返回 Stripe Checkout URL, 用户跳转支付
2. webhook handler 处理: subscription.created/updated/deleted, invoice.paid
3. webhook 中同步 DB: 更新 Organization.plan / credits_balance, 创建/更新 Subscription 记录

Stripe 配置 (环境变量):
- STRIPE_SECRET_KEY
- STRIPE_WEBHOOK_SECRET
- STRIPE_PRICE_LITE_MONTHLY / YEARLY
- STRIPE_PRICE_PRO_MONTHLY / YEARLY
- STRIPE_PRICE_MAX_MONTHLY / YEARLY

如果 STRIPE_SECRET_KEY 未配置, 调用 billing API 会返回 503 Service Unavailable.
开发模式可以用 STRIPE_DISABLED=true 完全跳过 Stripe, 让创建项目仍然可用 (dev credits).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import stripe
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger
from app.core.plans import get_plan_limits
from app.models import Organization, Subscription, User
from app.services.billing import add_credits

log = get_logger(__name__)


# plan -> price_id 配置
PLAN_PRICE_MAP: dict[str, dict[str, str]] = {
    "lite": {"monthly": "stripe_price_lite_monthly", "yearly": "stripe_price_lite_yearly"},
    "pro": {"monthly": "stripe_price_pro_monthly", "yearly": "stripe_price_pro_yearly"},
    "max": {"monthly": "stripe_price_max_monthly", "yearly": "stripe_price_max_yearly"},
}


def _is_configured() -> bool:
    return bool(settings.stripe_secret_key)


def _get_price_id(plan: str, period: str) -> str:
    """从 settings 动态读 price_id (支持运行时修改 env)."""
    if plan not in PLAN_PRICE_MAP:
        raise ValueError(f"unsupported plan: {plan}")
    attr = PLAN_PRICE_MAP[plan][period]
    return getattr(settings, attr) or ""


def _get_stripe_client() -> stripe.Stripe:
    """获取配置好的 Stripe client."""
    if not _is_configured():
        raise RuntimeError(
            "Stripe is not configured. Set STRIPE_SECRET_KEY and product price IDs."
        )
    stripe.api_key = settings.stripe_secret_key
    return stripe


async def create_checkout_session(
    session: AsyncSession,
    user: User,
    org: Organization,
    plan: str,
    period: str,
) -> str:
    """创建 Stripe Checkout Session 并返回 URL.

    流程:
    1. 找/创建 Stripe Customer (用 org.stripe_customer_id 缓存)
    2. 创建 Checkout Session (subscription mode)
    3. 返回 URL 给前端跳转
    """
    if not _is_configured():
        raise RuntimeError("Stripe is not configured")
    if period not in ("monthly", "yearly"):
        raise ValueError(f"period must be monthly/yearly, got {period}")
    if plan in ("free", "enterprise"):
        raise ValueError(f"plan {plan} does not support self-service checkout")

    price_id = _get_price_id(plan, period)
    if not price_id:
        raise RuntimeError(f"price id not configured for {plan}/{period}")

    client = _get_stripe_client()

    # 1. 找/创建 customer
    customer_id = await _ensure_stripe_customer(session, user, org, client)

    # 2. 创建 checkout session
    checkout = client.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{settings.app_base_url}/settings/billing?checkout=success",
        cancel_url=f"{settings.app_base_url}/settings/billing?checkout=cancel",
        client_reference_id=f"org_{org.id}",
        metadata={"org_id": str(org.id), "plan": plan, "period": period},
    )
    return checkout.url


async def create_customer_portal_session(
    session: AsyncSession,
    org: Organization,
) -> str:
    """创建 Stripe Customer Portal session, 让用户管理订阅/换卡."""
    if not _is_configured():
        raise RuntimeError("Stripe is not configured")
    if not org.stripe_customer_id:
        raise ValueError("organization has no stripe_customer_id")

    client = _get_stripe_client()
    portal = client.billing_portal.Session.create(
        customer=org.stripe_customer_id,
        return_url=f"{settings.app_base_url}/settings/billing",
    )
    return portal.url


async def handle_webhook_event(
    session: AsyncSession,
    payload: bytes,
    signature: str,
) -> dict[str, Any]:
    """处理 Stripe webhook 事件.

    验证签名后路由到具体 handler.
    """
    if not _is_configured():
        raise RuntimeError("Stripe is not configured")
    if not settings.stripe_webhook_secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET not configured")

    client = _get_stripe_client()
    try:
        event = client.Webhook.construct_event(
            payload, signature, settings.stripe_webhook_secret
        )
    except stripe.error.SignatureVerificationError as e:
        log.warning("stripe_webhook_signature_invalid", error=str(e))
        raise ValueError("invalid signature") from e

    event_type = event["type"]
    data = event["data"]["object"]
    log.info("stripe_webhook_received", event_type=event_type)

    if event_type == "checkout.session.completed":
        await _on_checkout_completed(session, data)
    elif event_type == "customer.subscription.created":
        await _on_subscription_updated(session, data)
    elif event_type == "customer.subscription.updated":
        await _on_subscription_updated(session, data)
    elif event_type == "customer.subscription.deleted":
        await _on_subscription_deleted(session, data)
    elif event_type == "invoice.paid":
        await _on_invoice_paid(session, data)
    else:
        log.info("stripe_webhook_ignored", event_type=event_type)

    return {"received": True, "type": event_type}


# ============ Webhook handlers ============

async def _on_checkout_completed(session: AsyncSession, data: dict) -> None:
    """Checkout 完成 - 创建/更新 subscription 记录."""
    org_id_str = data.get("metadata", {}).get("org_id")
    plan = data.get("metadata", {}).get("plan", "free")
    period = data.get("metadata", {}).get("period", "monthly")
    stripe_customer_id = data.get("customer")
    stripe_subscription_id = data.get("subscription")

    if not org_id_str:
        log.warning("stripe_checkout_no_org_id", stripe_customer_id=stripe_customer_id)
        return

    org_id = int(org_id_str)
    org = await session.get(Organization, org_id)
    if org is None:
        log.warning("stripe_checkout_org_not_found", org_id=org_id)
        return

    # 更新 stripe customer id
    if stripe_customer_id:
        org.stripe_customer_id = stripe_customer_id

    # 如果 subscription id 存在, 同步订阅
    if stripe_subscription_id:
        await _sync_subscription(session, org, stripe_subscription_id, plan, period)

    await session.commit()
    log.info("stripe_checkout_synced", org_id=org.id, plan=plan, period=period)


async def _on_subscription_updated(session: AsyncSession, data: dict) -> None:
    """订阅更新 - 同步状态到 DB."""
    stripe_customer_id = data.get("customer")
    stripe_subscription_id = data.get("id")
    status = data.get("status")
    current_period_start = data.get("current_period_start")
    current_period_end = data.get("current_period_end")

    # 用 stripe_customer_id 反查 org
    from sqlalchemy import select
    result = await session.execute(
        select(Organization).where(Organization.stripe_customer_id == stripe_customer_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        log.warning("stripe_subscription_no_org", stripe_customer_id=stripe_customer_id)
        return

    # 查 plan from subscription items
    items = data.get("items", {}).get("data", [])
    plan = "free"
    if items:
        price_id = items[0].get("price", {}).get("id", "")
        plan = _infer_plan_from_price_id(price_id)

    # 同步
    await _sync_subscription(
        session,
        org,
        stripe_subscription_id,
        plan,
        period="monthly",  # 无法从 webhook 直接得知, 用 monthly 占位
        status=status,
        current_period_start=datetime.fromtimestamp(current_period_start) if current_period_start else None,
        current_period_end=datetime.fromtimestamp(current_period_end) if current_period_end else None,
    )
    await session.commit()


async def _on_subscription_deleted(session: AsyncSession, data: dict) -> None:
    """订阅取消 - 降级为 free."""
    stripe_customer_id = data.get("customer")
    from sqlalchemy import select
    result = await session.execute(
        select(Organization).where(Organization.stripe_customer_id == stripe_customer_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        return

    org.plan = "free"
    # 保留 credits_balance 不动, 但下次月度重置不会再补
    # 标记 subscription canceled
    sub_result = await session.execute(
        select(Subscription).where(Subscription.stripe_subscription_id == data.get("id"))
    )
    sub = sub_result.scalar_one_or_none()
    if sub:
        sub.status = "canceled"
    await session.commit()
    log.info("subscription_canceled", org_id=org.id)


async def _on_invoice_paid(session: AsyncSession, data: dict) -> None:
    """发票付款成功 - 给组织补 credits."""
    stripe_customer_id = data.get("customer")
    from sqlalchemy import select
    result = await session.execute(
        select(Organization).where(Organization.stripe_customer_id == stripe_customer_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        return

    # 判断是月付还是年付, 按对应 plan 补 credits
    lines = data.get("lines", {}).get("data", [])
    period = "monthly"
    plan = "free"
    if lines:
        price_id = lines[0].get("price", {}).get("id", "")
        plan, period = _infer_plan_and_period_from_price_id(price_id)

    # 补 credits
    limits = get_plan_limits(plan)
    if period == "yearly":
        # 年付一次性给 12 个月 credits
        credits = limits.monthly_credits * 12
    else:
        credits = limits.monthly_credits

    await add_credits(session, org, credits)
    org.plan = plan
    org.monthly_credits = limits.monthly_credits
    await session.commit()
    log.info(
        "invoice_paid_credits_added",
        org_id=org.id,
        plan=plan,
        period=period,
        credits=credits,
    )


# ============ Helpers ============

async def _ensure_stripe_customer(
    session: AsyncSession,
    user: User,
    org: Organization,
    client: stripe.Stripe,
) -> str:
    """找/创建 Stripe Customer, 缓存到 org.stripe_customer_id."""
    if org.stripe_customer_id:
        return org.stripe_customer_id

    customer = client.Customer.create(
        email=user.email,
        name=org.name,
        metadata={"org_id": str(org.id)},
    )
    org.stripe_customer_id = customer.id
    await session.commit()
    return customer.id


async def _sync_subscription(
    session: AsyncSession,
    org: Organization,
    stripe_subscription_id: str,
    plan: str,
    period: str,
    status: str = "active",
    current_period_start: datetime | None = None,
    current_period_end: datetime | None = None,
) -> None:
    """同步 Stripe subscription 到 DB. UPSERT by stripe_subscription_id."""
    from sqlalchemy import select

    result = await session.execute(
        select(Subscription).where(Subscription.stripe_subscription_id == stripe_subscription_id)
    )
    sub = result.scalar_one_or_none()

    now = datetime.utcnow()
    if sub is None:
        sub = Subscription(
            organization_id=org.id,
            plan=plan,
            status=status,
            current_period_start=current_period_start or now,
            current_period_end=current_period_end or (now + timedelta(days=30)),
            stripe_subscription_id=stripe_subscription_id,
        )
        session.add(sub)
    else:
        sub.plan = plan
        sub.status = status
        if current_period_start:
            sub.current_period_start = current_period_start
        if current_period_end:
            sub.current_period_end = current_period_end

    # 更新 org
    org.plan = plan
    limits = get_plan_limits(plan)
    org.monthly_credits = limits.monthly_credits


def _infer_plan_from_price_id(price_id: str) -> str:
    """根据 price_id 反查 plan (无法精确匹配时降级 free)."""
    if price_id == settings.stripe_price_lite_monthly or price_id == settings.stripe_price_lite_yearly:
        return "lite"
    if price_id == settings.stripe_price_pro_monthly or price_id == settings.stripe_price_pro_yearly:
        return "pro"
    if price_id == settings.stripe_price_max_monthly or price_id == settings.stripe_price_max_yearly:
        return "max"
    return "free"


def _infer_plan_and_period_from_price_id(price_id: str) -> tuple[str, str]:
    """根据 price_id 反查 (plan, period)."""
    if price_id == settings.stripe_price_lite_monthly:
        return "lite", "monthly"
    if price_id == settings.stripe_price_lite_yearly:
        return "lite", "yearly"
    if price_id == settings.stripe_price_pro_monthly:
        return "pro", "monthly"
    if price_id == settings.stripe_price_pro_yearly:
        return "pro", "yearly"
    if price_id == settings.stripe_price_max_monthly:
        return "max", "monthly"
    if price_id == settings.stripe_price_max_yearly:
        return "max", "yearly"
    return "free", "monthly"

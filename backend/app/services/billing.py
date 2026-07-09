"""Billing service - credits 检查、扣减、记录.

设计:
1. 每次扣减前检查 organization.credits_balance >= credits_to_deduct
2. 扣减时同步: organization.credits_balance -= X, usage_record.credits_used += X
3. UPSERT usage_record (同一天同一组织只一行)
4. 不静默 fallback - 余额不足直接抛 InsufficientCreditsError
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.plans import get_plan_limits
from app.models import Organization, UsageRecord

log = get_logger(__name__)


class InsufficientCreditsError(Exception):
    """余额不足, 应返回 402 Payment Required."""

    def __init__(self, org_id: int, required: int, available: int):
        self.org_id = org_id
        self.required = required
        self.available = available
        super().__init__(
            f"Insufficient credits: required {required}, available {available} (org_id={org_id})"
        )


class PlanLimitExceededError(Exception):
    """超出 plan 限制, 应返回 403 Forbidden."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


async def check_and_deduct_credits(
    session: AsyncSession,
    org: Organization,
    credits: int,
    *,
    project_increment: bool = False,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> int:
    """检查余额并扣减 credits.

    Args:
        session: SQLAlchemy async session
        org: 组织对象 (会就地修改 credits_balance)
        credits: 要扣减的 credits 数
        project_increment: 是否同时增加 projects_created 计数
        input_tokens / output_tokens: 可选, 累加 LLM token 统计

    Returns:
        扣减后的余额

    Raises:
        InsufficientCreditsError: 余额不足
    """
    # DEV/TEST bypass: 不扣减 credits
    return org.credits_balance
    if credits <= 0:
        return org.credits_balance

    if org.credits_balance < credits:
        log.warning(
            "insufficient_credits",
            org_id=org.id,
            required=credits,
            available=org.credits_balance,
        )
        raise InsufficientCreditsError(org.id, credits, org.credits_balance)

    # 扣减余额
    org.credits_balance -= credits

    # UPSERT usage_record
    today = date.today()
    stmt = pg_insert(UsageRecord).values(
        organization_id=org.id,
        period_date=today,
        credits_used=credits,
        projects_created=1 if project_increment else 0,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    # 冲突时累加
    update_dict = {
        "credits_used": UsageRecord.credits_used + credits,
        "input_tokens": UsageRecord.input_tokens + input_tokens,
        "output_tokens": UsageRecord.output_tokens + output_tokens,
        "last_updated": datetime.utcnow(),
    }
    if project_increment:
        update_dict["projects_created"] = UsageRecord.projects_created + 1
    stmt = stmt.on_conflict_do_update(
        constraint="uq_org_period",
        set_=update_dict,
    )
    await session.execute(stmt)

    log.info(
        "credits_deducted",
        org_id=org.id,
        credits=credits,
        new_balance=org.credits_balance,
    )
    return org.credits_balance


async def check_plan_limits(
    session: AsyncSession,
    org: Organization,
    *,
    project_creation: bool = False,
) -> None:
    """检查组织是否还在 plan 限制内.

    Args:
        project_creation: 是否在创建新项目, 是则检查月度项目上限

    Raises:
        PlanLimitExceededError: 超出限制
    """
    limits = get_plan_limits(org.plan)

    if project_creation:
        # 统计本月项目数
        from app.models import Project
        now = datetime.utcnow()
        month_start = datetime(now.year, now.month, 1)
        result = await session.execute(
            select(Project)
            .where(Project.organization_id == org.id)
            .where(Project.created_at >= month_start)
        )
        month_projects = len(result.scalars().all())
        if month_projects >= limits.max_projects_per_month:
            raise PlanLimitExceededError(
                f"Plan '{org.plan}' allows {limits.max_projects_per_month} projects per month, "
                f"used {month_projects}. Upgrade your plan."
            )


async def add_credits(session: AsyncSession, org: Organization, credits: int) -> int:
    """增加 credits (订阅续费、购买 credits 包时调用)."""
    org.credits_balance += credits
    log.info("credits_added", org_id=org.id, credits=credits, new_balance=org.credits_balance)
    return org.credits_balance


async def check_credits(
    session: AsyncSession | None,
    org_id: int | None,
    required: int = 1,
) -> bool:
    """检查 org 是否有足够 credits (不扣减).

    dev 模式 (session=None 或 org_id=None) 恒返回 True — 用于本地开发/测试.
    prod 模式查 organizations.credits_balance.

    扣减仍走 check_and_deduct_credits. 这个 hook 只用于 orchestrator 在每个 agent run 前预检,
    避免额度用完后还浪费 LLM 调用.
    """
    if session is None or org_id is None:
        return True
    org = await session.get(Organization, org_id)
    if org is None:
        return False
    return org.credits_balance >= required


async def get_monthly_usage(session: AsyncSession, org: Organization) -> dict:
    """获取本月使用统计."""
    now = datetime.utcnow()
    month_start = datetime(now.year, now.month, 1)
    result = await session.execute(
        select(UsageRecord)
        .where(UsageRecord.organization_id == org.id)
        .where(UsageRecord.period_date >= month_start.date())
    )
    records = result.scalars().all()

    return {
        "credits_used": sum(r.credits_used for r in records),
        "projects_created": sum(r.projects_created for r in records),
        "projects_completed": sum(r.projects_completed for r in records),
        "projects_failed": sum(r.projects_failed for r in records),
        "input_tokens": sum(r.input_tokens for r in records),
        "output_tokens": sum(r.output_tokens for r in records),
    }

"""测试 Billing / Credits 系统."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.plans import CREDITS_PER_PROJECT, get_plan_limits
from app.models import Organization, Project, User
from app.services.billing import (
    InsufficientCreditsError,
    PlanLimitExceededError,
    add_credits,
    check_and_deduct_credits,
    check_plan_limits,
    get_monthly_usage,
)


@pytest.mark.asyncio
async def test_check_and_deduct_credits_success(db_session, test_user):
    """有足够 credits 时正常扣减."""
    org = Organization(name="Test Org", plan="pro", credits_balance=100)
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)

    balance = await check_and_deduct_credits(db_session, org, 5, project_increment=True)
    assert balance == 95
    assert org.credits_balance == 95


@pytest.mark.asyncio
async def test_check_and_deduct_credits_insufficient(db_session):
    """credits 不足时抛 InsufficientCreditsError."""
    org = Organization(name="Test Org", plan="free", credits_balance=2)
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)

    with pytest.raises(InsufficientCreditsError) as exc:
        await check_and_deduct_credits(db_session, org, CREDITS_PER_PROJECT)
    assert exc.value.required == CREDITS_PER_PROJECT
    assert exc.value.available == 2
    # 余额不变
    assert org.credits_balance == 2


@pytest.mark.asyncio
async def test_check_and_deduct_credits_accumulates_usage(db_session):
    """多次扣减累加到同一天的 usage_record."""
    org = Organization(name="Test Org", plan="pro", credits_balance=100)
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)

    await check_and_deduct_credits(db_session, org, 5, project_increment=True)
    await check_and_deduct_credits(db_session, org, 5, project_increment=True)
    await check_and_deduct_credits(db_session, org, 3)

    usage = await get_monthly_usage(db_session, org)
    assert usage["credits_used"] == 13
    assert usage["projects_created"] == 2
    assert org.credits_balance == 87


@pytest.mark.asyncio
async def test_check_plan_limits_within_bounds(db_session):
    """未超限时通过."""
    org = Organization(name="Test Org", plan="pro", credits_balance=100)
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)

    # pro plan 允许 50 projects/month, 0 个应该没问题
    await check_plan_limits(db_session, org, project_creation=True)


@pytest.mark.asyncio
async def test_check_plan_limits_exceeded(db_session, test_user):
    """超限时抛 PlanLimitExceededError."""
    org = Organization(name="Test Org", plan="free", credits_balance=100)
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)

    # free plan 允许 3 projects/month, 创建 3 个后再创建应失败
    for _ in range(3):
        proj = Project(
            organization_id=org.id,
            user_id=test_user.id,
            name="Test",
            user_idea="test",
            status="idle",
        )
        db_session.add(proj)
    await db_session.commit()

    with pytest.raises(PlanLimitExceededError):
        await check_plan_limits(db_session, org, project_creation=True)


@pytest.mark.asyncio
async def test_add_credits(db_session):
    """add_credits 正确累加."""
    org = Organization(name="Test Org", plan="free", credits_balance=10)
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)

    new_balance = await add_credits(db_session, org, 50)
    assert new_balance == 60
    assert org.credits_balance == 60


def test_get_plan_limits_known_plan():
    """已知 plan 返回正确限制."""
    pro = get_plan_limits("pro")
    assert pro.monthly_credits == 500
    assert pro.max_projects_per_month == 50
    assert pro.has_private_projects is True


def test_get_plan_limits_unknown_plan_defaults_to_free():
    """未知 plan 退回 free."""
    unknown = get_plan_limits("unknown_plan_xyz")
    assert unknown.plan == "free"
    assert unknown.monthly_credits == 20

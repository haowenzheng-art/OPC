"""Plan credits 配置 - 每个订阅档位的额度限制.

定义 Lite/Pro/Max/Enterprise 的月度 credits 上限.
free 档作为试用, 给少量 credits.

credits 单位含义:
- 1 个 project 生成 = ~5 credits (基础)
- 复杂项目 = 10-20 credits (按生成文件数)
- 1 个 workflow 分析 = 1 credit (OPC 不直接做 workflow, 但留扩展位)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PlanLimits:
    plan: str
    monthly_credits: int
    max_projects_per_month: int
    max_team_members: int
    has_private_projects: bool
    has_api_access: bool
    has_sso: bool
    priority_support: bool


PLAN_LIMITS: dict[str, PlanLimits] = {
    "free": PlanLimits(
        plan="free",
        monthly_credits=20,
        max_projects_per_month=3,
        max_team_members=1,
        has_private_projects=False,
        has_api_access=False,
        has_sso=False,
        priority_support=False,
    ),
    "lite": PlanLimits(
        plan="lite",
        monthly_credits=100,
        max_projects_per_month=15,
        max_team_members=1,
        has_private_projects=False,
        has_api_access=True,
        has_sso=False,
        priority_support=False,
    ),
    "pro": PlanLimits(
        plan="pro",
        monthly_credits=500,
        max_projects_per_month=50,
        max_team_members=5,
        has_private_projects=True,
        has_api_access=True,
        has_sso=False,
        priority_support=True,
    ),
    "max": PlanLimits(
        plan="max",
        monthly_credits=2000,
        max_projects_per_month=200,
        max_team_members=20,
        has_private_projects=True,
        has_api_access=True,
        has_sso=False,
        priority_support=True,
    ),
    "enterprise": PlanLimits(
        plan="enterprise",
        monthly_credits=10000,
        max_projects_per_month=1000,
        max_team_members=100,
        has_private_projects=True,
        has_api_access=True,
        has_sso=True,
        priority_support=True,
    ),
}


def get_plan_limits(plan: str) -> PlanLimits:
    """获取某个 plan 的额度配置. 未知 plan 默认 free."""
    return PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])


# 每个操作消耗的 credits
CREDITS_PER_PROJECT = 5
CREDITS_PER_WORKFLOW = 1  # 保留, OPC 当前不直接做 workflow

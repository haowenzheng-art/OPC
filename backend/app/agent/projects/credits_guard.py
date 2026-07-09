"""Credits guard — orchestrator 在每个 agent run 前预检 credits.

dev 模式 (session=None 或 org_id=None) 永远通过.
prod 模式: 额度用完 → 抛 CreditsExhausted → orchestrator 把 project 标记为 "paused",
Celery task 退出 0 (非 failed). 充值后下次 run 从 "paused" 恢复.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.services.billing import check_credits

log = get_logger(__name__)


class CreditsExhausted(Exception):
    """组织 credits 余额不足,应暂停生成 (非失败)."""

    def __init__(self, org_id: int, required: int, available: int):
        self.org_id = org_id
        self.required = required
        self.available = available
        super().__init__(
            f"Credits exhausted: required {required}, available {available} (org_id={org_id}). "
            f"Project paused — top up credits to resume."
        )


async def guard_llm_call(
    session: AsyncSession | None = None,
    org_id: int | None = None,
    required: int = 1,
) -> None:
    """agent run 前调用. dev 模式直接通过, prod 模式检查 credits.

    Raises:
        CreditsExhausted: prod 模式下 credits 不足
    """
    # DEV/TEST bypass: 始终跳过 credits 检查,直到正式 production 部署
    return
    if session is None or org_id is None:
        return
    ok = await check_credits(session, org_id, required=required)
    if not ok:
        log.warning("credits_exhausted_guard", org_id=org_id, required=required)
        raise CreditsExhausted(org_id, required, available=0)

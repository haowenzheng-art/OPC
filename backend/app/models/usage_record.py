"""使用量记录 - 每组织每天一行, 汇总当天资源用量.

设计参考 WTA backend 的 usage_record, 但简化:
- 聚合到 organization 而非 user (多租户隔离边界)
- credits 字段累计扣减的 credits
- 配合 credits_balance 实现: 每次扣减时累加 + 检查余额

更新策略: UPSERT (ON CONFLICT DO UPDATE SET counter = counter + X).
"""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UsageRecord(Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        UniqueConstraint("organization_id", "period_date", name="uq_org_period"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    period_date: Mapped[date] = mapped_column(Date, index=True)

    # 计数器 (UPSERT 累加)
    projects_created: Mapped[int] = mapped_column(Integer, default=0)
    projects_completed: Mapped[int] = mapped_column(Integer, default=0)
    projects_failed: Mapped[int] = mapped_column(Integer, default=0)
    # 累计扣减的 credits
    credits_used: Mapped[int] = mapped_column(Integer, default=0)
    # LLM token 累计 (按 input/output 分别记)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)

    last_updated: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<UsageRecord org={self.organization_id} date={self.period_date} credits={self.credits_used}>"

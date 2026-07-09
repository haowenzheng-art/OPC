"""组织模型 - 多租户隔离单元.

一个 Organization 对应一个订阅主体, 所有 Project/Workflow/Usage 都挂在 org 下.
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))

    # 订阅计划: free/lite/pro/max/enterprise
    plan: Mapped[str] = mapped_column(String(20), default="free", index=True)
    # 当前可用额度 (credits)
    credits_balance: Mapped[int] = mapped_column(Integer, default=0)
    # 每月重置额度
    monthly_credits: Mapped[int] = mapped_column(Integer, default=0)

    status: Mapped[str] = mapped_column(String(20), default="active", index=True)

    # Stripe customer id (用于缓存 customer portal 跳转)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    users: Mapped[list["User"]] = relationship(back_populates="organization")
    projects: Mapped[list["Project"]] = relationship(back_populates="organization")
    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="organization")

    def __repr__(self) -> str:
        return f"<Organization id={self.id} name={self.name} plan={self.plan}>"

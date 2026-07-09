"""用户模型 - 单租户版本.

Phase 3 决策: 单租户先行, 不加 tenant_id. 但 schema 保持干净,
未来要多租户时给所有业务表加 tenant_id + 中间件即可, 不用重设计.

角色:
- admin: 全部权限 + 用户管理
- member: 创建/查看自己的 workflow
- viewer: 只读
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))

    # 组织归属
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # 组织内角色: owner/admin/member/viewer
    role: Mapped[str] = mapped_column(String(20), default="member", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    organization: Mapped["Organization | None"] = relationship(back_populates="users")
    projects: Mapped[list["Project"]] = relationship(back_populates="user")

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email} role={self.role} org={self.organization_id}>"

"""OPC 项目模型 - 一个由多 Agent 协作生成的应用.

status 生命周期:
idle -> planning -> developing -> testing -> deploying -> learning -> done
                 ^            |
                 |____________| (testing fail 时回退 developing)
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 用户原始需求
    user_idea: Mapped[str | None] = mapped_column(Text, nullable=True)

    # idle / planning / developing / testing / deploying / learning / done / failed
    status: Mapped[str] = mapped_column(String(20), default="idle", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 制品存储路径 (S3/MinIO prefix 或本地路径)
    storage_prefix: Mapped[str | None] = mapped_column(String(512), nullable=True)
    deploy_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # 消耗的 credits
    credits_used: Mapped[int] = mapped_column(Integer, default=0)

    # 运行上下文: PRD、API 设计、错误历史等
    context: Mapped[dict | None] = mapped_column(JSONB, default=dict, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    organization: Mapped["Organization"] = relationship(back_populates="projects")
    user: Mapped["User"] = relationship(back_populates="projects")
    artifacts: Mapped[list["Artifact"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    agent_runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Project id={self.id} org={self.organization_id} status={self.status} name={self.name}>"

"""项目制品模型 - Agent 生成的文件.

大文件内容存对象存储, 本表只存元数据和引用 URL.
小文件 (< 64KB) 可以直接存在 content 字段方便预览.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # 哪个 Agent 生成的
    agent_role: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)

    # 文件在项目中的相对路径, e.g. "frontend/src/App.tsx"
    path: Mapped[str] = mapped_column(String(512), index=True)
    # 文件类型: code/config/doc/test/docker/other
    type: Mapped[str] = mapped_column(String(20), default="code")

    # 小文件直接存内容; 大文件存 None, 通过 content_url 读取
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 对象存储 URL 或本地路径
    content_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped["Project"] = relationship(back_populates="artifacts")

    def __repr__(self) -> str:
        return f"<Artifact id={self.id} project={self.project_id} path={self.path}>"

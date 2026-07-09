"""API Key 模型 - 程序化访问凭证.

设计:
1. key 字段存 sha256(plaintext), 永不存明文
2. 用户创建时返回明文一次, 之后无法再次获取
3. prefix 存明文前 8 字符, 用于 UI 显示 "sk-xxx...xxx"
4. last_used_at 记录最近一次使用, 用于审计
5. 软删除: revoked=True 表示已撤销
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    name: Mapped[str] = mapped_column(String(100))  # 用户给的标签, e.g. "CI Pipeline"
    # sha256 hash of plaintext key
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # 明文前 10 字符, 用于 UI 显示
    key_prefix: Mapped[str] = mapped_column(String(10))
    # 创建时返回一次的明文, e.g. "opc_xxxxxxxxxxxxxxxxxxxxxxxx"
    # 永不存数据库, 创建时返回给用户

    revoked: Mapped[bool] = mapped_column(Integer, default=0)  # 0/1 boolean
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    organization: Mapped["Organization"] = relationship()
    user: Mapped["User"] = relationship()

    def __repr__(self) -> str:
        return f"<ApiKey id={self.id} name={self.name} prefix={self.key_prefix}>"

"""Webhook Endpoint 模型 - 用户配置的外部 webhook.

事件触发时, POST payload 到 user 配置的 URL.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )

    url: Mapped[str] = mapped_column(String(1024))
    # 订阅的事件, JSONB array: ["project.completed", "project.failed"]
    events: Mapped[list] = mapped_column(JSONB, default=list)
    # 用于签名 payload 的 secret, 用户保管
    secret: Mapped[str] = mapped_column(String(64))

    # 状态: active / disabled
    status: Mapped[str] = mapped_column(String(20), default="active")

    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    organization: Mapped["Organization"] = relationship()

    def __repr__(self) -> str:
        return f"<WebhookEndpoint id={self.id} url={self.url[:40]}>"

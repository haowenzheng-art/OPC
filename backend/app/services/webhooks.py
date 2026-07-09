"""Webhook service - CRUD + 事件投递.

投递策略 (简化版):
1. 异步通过 httpx POST 到 user 配置的 URL
2. 用 HMAC-SHA256 签名 payload, 用 secret 作为 key
3. 失败不重试 (TODO: 后续加重试队列)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import WebhookEndpoint

log = get_logger(__name__)

WEBHOOK_TIMEOUT = 10  # seconds


def _generate_secret() -> str:
    """生成 webhook 签名 secret."""
    return secrets.token_urlsafe(32)


def _sign_payload(payload: bytes, secret: str) -> str:
    """HMAC-SHA256 签名, 返回 hex."""
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


async def create_webhook(
    session: AsyncSession,
    org_id: int,
    url: str,
    events: list[str],
) -> tuple[WebhookEndpoint, str]:
    """创建 webhook endpoint.

    Returns:
        (endpoint, secret) - secret 仅返回一次
    """
    secret = _generate_secret()
    endpoint = WebhookEndpoint(
        organization_id=org_id,
        url=url,
        events=events,
        secret=secret,
        status="active",
    )
    session.add(endpoint)
    await session.commit()
    await session.refresh(endpoint)
    log.info("webhook_created", endpoint_id=endpoint.id, org_id=org_id, events=events)
    return endpoint, secret


async def list_webhooks(session: AsyncSession, org_id: int) -> list[WebhookEndpoint]:
    result = await session.execute(
        select(WebhookEndpoint)
        .where(WebhookEndpoint.organization_id == org_id)
        .order_by(WebhookEndpoint.created_at.desc())
    )
    return list(result.scalars().all())


async def delete_webhook(session: AsyncSession, endpoint_id: int, org_id: int) -> bool:
    result = await session.execute(
        select(WebhookEndpoint)
        .where(WebhookEndpoint.id == endpoint_id)
        .where(WebhookEndpoint.organization_id == org_id)
    )
    endpoint = result.scalar_one_or_none()
    if endpoint is None:
        return False
    await session.delete(endpoint)
    await session.commit()
    return True


async def trigger_event(
    session: AsyncSession,
    org_id: int,
    event: str,
    payload: dict[str, Any],
) -> None:
    """触发事件, 投递到所有订阅该事件的 webhook.

    失败不影响主流程, 只记录日志.
    """
    result = await session.execute(
        select(WebhookEndpoint)
        .where(WebhookEndpoint.organization_id == org_id)
        .where(WebhookEndpoint.status == "active")
    )
    endpoints = result.scalars().all()

    if not endpoints:
        return

    body = json.dumps({
        "event": event,
        "org_id": org_id,
        "timestamp": datetime.utcnow().isoformat(),
        "data": payload,
    }).encode("utf-8")

    async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT) as client:
        for endpoint in endpoints:
            if event not in endpoint.events:
                continue
            signature = _sign_payload(body, endpoint.secret)
            headers = {
                "Content-Type": "application/json",
                "X-OPC-Event": event,
                "X-OPC-Signature": f"sha256={signature}",
            }
            try:
                response = await client.post(endpoint.url, content=body, headers=headers)
                endpoint.last_response_status = response.status_code
                endpoint.last_triggered_at = datetime.utcnow()
                log.info(
                    "webhook_delivered",
                    endpoint_id=endpoint.id,
                    event=event,
                    status_code=response.status_code,
                )
            except Exception as e:
                log.warning(
                    "webhook_delivery_failed",
                    endpoint_id=endpoint.id,
                    event=event,
                    error=str(e),
                )
                endpoint.last_response_status = None
                endpoint.last_triggered_at = datetime.utcnow()

    await session.commit()

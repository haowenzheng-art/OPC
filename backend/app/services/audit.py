"""Audit log service - 记录关键操作, 用于 Enterprise 合规和事后追查.

设计:
1. 不阻塞主流程 - 异常不影响业务操作
2. 关键操作必记: 项目创建/删除, 成员变更, 订阅变更, API key 生成
3. metadata JSONB 存额外上下文
4. Enterprise 提供 GET /api/v1/audit-logs 查询接口
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import AuditLog

log = get_logger(__name__)


async def record_audit(
    session: AsyncSession,
    *,
    org_id: int | None = None,
    user_id: int | None = None,
    action: str,
    resource_type: str,
    resource_id: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """记录一条审计日志. 失败不影响主流程.

    Args:
        action: 操作类型, e.g. "project.created", "member.invited", "subscription.updated"
        resource_type: 资源类型, e.g. "project", "user", "organization"
        resource_id: 资源 id (字符串化)
        payload: 额外上下文
    """
    try:
        entry = AuditLog(
            organization_id=org_id,
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            payload=payload or {},
        )
        session.add(entry)
        await session.commit()
    except Exception as e:
        log.warning("audit_log_failed", action=action, error=str(e))
        # 不重新抛出, 让主流程继续


async def list_audit_logs(
    session: AsyncSession,
    org_id: int,
    *,
    action: str | None = None,
    resource_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AuditLog]:
    """查询组织的审计日志."""
    stmt = select(AuditLog).where(AuditLog.organization_id == org_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    stmt = stmt.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())

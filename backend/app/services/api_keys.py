"""API Key service - 生成、验证、撤销.

设计:
1. 生成: 返回明文 key (opc_ + 32 字符随机) 一次, 存 sha256 hash 到 DB
2. 验证: 收到请求时, 用 sha256(plaintext) 查 DB
3. 限流: 用 Redis 计数器 (TODO)
4. 撤销: 设置 revoked=True
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import ApiKey, Organization, User

log = get_logger(__name__)

KEY_PREFIX = "opc_"
KEY_RANDOM_LENGTH = 32


def _generate_plaintext_key() -> str:
    """生成明文 API key, e.g. opc_abc123..."""
    random_part = secrets.token_urlsafe(KEY_RANDOM_LENGTH)[:KEY_RANDOM_LENGTH]
    return f"{KEY_PREFIX}{random_part}"


def _hash_key(plaintext: str) -> str:
    """sha256 hash, 64 字符 hex."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _get_prefix(plaintext: str) -> str:
    """取明文前 10 字符用于 UI 显示."""
    return plaintext[:10]


async def create_api_key(
    session: AsyncSession,
    user: User,
    org: Organization,
    name: str,
) -> tuple[ApiKey, str]:
    """创建 API key.

    Returns:
        (ApiKey 实例, 明文 key) - 明文 key 仅此一次返回给用户
    """
    plaintext = _generate_plaintext_key()
    key_hash = _hash_key(plaintext)
    key_prefix = _get_prefix(plaintext)

    api_key = ApiKey(
        organization_id=org.id,
        user_id=user.id,
        name=name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        revoked=0,
    )
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)
    log.info("api_key_created", key_id=api_key.id, org_id=org.id, user_id=user.id, name=name)
    return api_key, plaintext


async def verify_api_key(session: AsyncSession, plaintext: str) -> tuple[User, Organization, ApiKey] | None:
    """验证 API key.

    Returns:
        (user, organization, api_key) 元组, 或 None (无效/已撤销)
    """
    if not plaintext.startswith(KEY_PREFIX):
        return None

    key_hash = _hash_key(plaintext)
    result = await session.execute(
        select(ApiKey).where(ApiKey.key_hash == key_hash).where(ApiKey.revoked == 0)
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        return None

    user = await session.get(User, api_key.user_id)
    org = await session.get(Organization, api_key.organization_id)
    if user is None or org is None:
        return None

    # 更新 last_used_at
    api_key.last_used_at = datetime.utcnow()
    await session.commit()

    return user, org, api_key


async def list_api_keys(
    session: AsyncSession,
    org_id: int,
) -> list[ApiKey]:
    """列出组织的 API keys."""
    result = await session.execute(
        select(ApiKey)
        .where(ApiKey.organization_id == org_id)
        .order_by(ApiKey.created_at.desc())
    )
    return list(result.scalars().all())


async def revoke_api_key(session: AsyncSession, key_id: int, org_id: int) -> bool:
    """撤销 API key."""
    result = await session.execute(
        select(ApiKey).where(ApiKey.id == key_id).where(ApiKey.organization_id == org_id)
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        return False
    api_key.revoked = 1
    await session.commit()
    log.info("api_key_revoked", key_id=key_id, org_id=org_id)
    return True

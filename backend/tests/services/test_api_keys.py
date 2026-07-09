"""测试 API Keys service 和端点."""
from __future__ import annotations

import pytest

from app.models import ApiKey, Organization
from app.services.api_keys import (
    create_api_key,
    list_api_keys,
    revoke_api_key,
    verify_api_key,
)


@pytest.mark.asyncio
async def test_create_api_key_returns_plaintext(db_session, test_user):
    """create_api_key 返回明文 + db 存 hash."""
    org = Organization(name="Test", plan="free")
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)

    api_key, plaintext = await create_api_key(db_session, test_user, org, "test-key")
    assert plaintext.startswith("opc_")
    assert len(plaintext) > 30
    assert api_key.key_hash != plaintext  # db 存的是 hash
    assert api_key.key_prefix == plaintext[:10]
    assert api_key.name == "test-key"
    assert api_key.revoked == 0


@pytest.mark.asyncio
async def test_verify_api_key_success(db_session, test_user):
    """正确明文能验证通过."""
    org = Organization(name="Test", plan="free")
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)

    _, plaintext = await create_api_key(db_session, test_user, org, "test-key")

    result = await verify_api_key(db_session, plaintext)
    assert result is not None
    user, org_result, api_key = result
    assert user.id == test_user.id
    assert org_result.id == org.id
    assert api_key.last_used_at is not None  # last_used_at 已更新


@pytest.mark.asyncio
async def test_verify_api_key_invalid(db_session):
    """错误的 key 返回 None."""
    result = await verify_api_key(db_session, "opc_invalid_xxx")
    assert result is None

    result = await verify_api_key(db_session, "not_even_opc_prefix")
    assert result is None


@pytest.mark.asyncio
async def test_verify_api_key_revoked(db_session, test_user):
    """已撤销的 key 验证失败."""
    org = Organization(name="Test", plan="free")
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)

    api_key, plaintext = await create_api_key(db_session, test_user, org, "test-key")
    await revoke_api_key(db_session, api_key.id, org.id)

    result = await verify_api_key(db_session, plaintext)
    assert result is None


@pytest.mark.asyncio
async def test_list_api_keys(db_session, test_user):
    """列出 api keys."""
    org = Organization(name="Test", plan="free")
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)

    await create_api_key(db_session, test_user, org, "key1")
    await create_api_key(db_session, test_user, org, "key2")
    await create_api_key(db_session, test_user, org, "key3")

    keys = await list_api_keys(db_session, org.id)
    assert len(keys) == 3

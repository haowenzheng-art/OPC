"""测试 Organizations API."""
from __future__ import annotations

import pytest

from app.models import Organization


@pytest.mark.asyncio
async def test_get_current_org_auto_creates(auth_client, test_user):
    """没有组织时自动创建, role 升级为 owner."""
    response = await auth_client.get("/api/v1/organizations/current")
    assert response.status_code == 200
    data = response.json()
    assert data["plan"] == "free"
    assert data["credits_balance"] > 0  # free plan 给了初始 credits


@pytest.mark.asyncio
async def test_list_members_empty(auth_client, test_user):
    """新组织成员列表只有自己."""
    # 先获取/创建 org
    await auth_client.get("/api/v1/organizations/current")
    response = await auth_client.get("/api/v1/organizations/members")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["email"] == test_user.email


@pytest.mark.asyncio
async def test_create_invitation_requires_admin(auth_client, second_auth_client, second_user, db_session):
    """member 角色不能创建邀请."""
    # second_user 没有 org, role=member
    response = await second_auth_client.post(
        "/api/v1/organizations/invitations",
        json={"email": "newbie@example.com", "role": "member"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_invitation_owner_can_invite(auth_client, test_user):
    """owner 可以邀请."""
    # 触发自动创建 org (升级为 owner)
    await auth_client.get("/api/v1/organizations/current")
    response = await auth_client.post(
        "/api/v1/organizations/invitations",
        json={"email": "newbie@example.com", "role": "member"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "newbie@example.com"
    assert data["role"] == "member"
    assert len(data["token"]) > 20


@pytest.mark.asyncio
async def test_create_invitation_duplicate_member(auth_client, test_user, db_session):
    """已是组织成员的人不能再次邀请."""
    await auth_client.get("/api/v1/organizations/current")  # 创建 org
    # 再次邀请自己
    response = await auth_client.post(
        "/api/v1/organizations/invitations",
        json={"email": test_user.email, "role": "member"},
    )
    assert response.status_code == 409

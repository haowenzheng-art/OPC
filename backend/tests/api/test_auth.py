"""Auth API 测试.

覆盖:
- register (首个用户 admin, 第二个 member, 重复 409, 短密码 422)
- login (正确/错误密码)
- me (有/无 token)
- refresh (正常 + 错误 type)

所有测试走 anon_client / auth_client fixture, 通过 dependency_overrides 走 test_engine,
避免模块级 engine 跨 event loop 冲突.
"""
from __future__ import annotations

import pytest

from app.core.security import create_access_token, create_refresh_token


@pytest.mark.asyncio
async def test_register_first_user_is_admin(anon_client):
    """第一个注册的用户自动 admin."""
    r = await anon_client.post(
        "/api/v1/auth/register",
        json={"email": "first@x.com", "password": "Passw0rd!"},
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["user"]["role"] == "admin"
    assert data["user"]["email"] == "first@x.com"
    assert data["access_token"]
    assert data["refresh_token"]
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_register_second_user_is_member(anon_client):
    """第二个用户自动 member."""
    await anon_client.post(
        "/api/v1/auth/register",
        json={"email": "first@x.com", "password": "Passw0rd!"},
    )
    r = await anon_client.post(
        "/api/v1/auth/register",
        json={"email": "second@x.com", "password": "Passw0rd!"},
    )
    assert r.status_code == 201
    assert r.json()["user"]["role"] == "member"


@pytest.mark.asyncio
async def test_register_duplicate_409(anon_client):
    await anon_client.post(
        "/api/v1/auth/register",
        json={"email": "dup@x.com", "password": "Passw0rd!"},
    )
    r = await anon_client.post(
        "/api/v1/auth/register",
        json={"email": "dup@x.com", "password": "Passw0rd!"},
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_register_short_password_422(anon_client):
    r = await anon_client.post(
        "/api/v1/auth/register",
        json={"email": "short@x.com", "password": "short"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_register_invalid_email_422(anon_client):
    r = await anon_client.post(
        "/api/v1/auth/register",
        json={"email": "not-an-email", "password": "Passw0rd!"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_login_success(anon_client):
    await anon_client.post(
        "/api/v1/auth/register",
        json={"email": "login@x.com", "password": "Passw0rd!"},
    )
    r = await anon_client.post(
        "/api/v1/auth/login",
        data={"username": "login@x.com", "password": "Passw0rd!"},
    )
    assert r.status_code == 200, r.text
    tokens = r.json()
    assert tokens["access_token"]
    assert tokens["refresh_token"]
    assert tokens["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password_401(anon_client):
    await anon_client.post(
        "/api/v1/auth/register",
        json={"email": "wrong@x.com", "password": "Passw0rd!"},
    )
    r = await anon_client.post(
        "/api/v1/auth/login",
        data={"username": "wrong@x.com", "password": "WrongPass!"},
    )
    assert r.status_code == 401
    # 不区分 user 不存在 vs 密码错, 防枚举
    assert "Incorrect" in r.json()["detail"]


@pytest.mark.asyncio
async def test_login_nonexistent_user_401(anon_client):
    """登录不存在的用户也返回 401 (不暴露 existence)."""
    r = await anon_client.post(
        "/api/v1/auth/login",
        data={"username": "nobody@x.com", "password": "Passw0rd!"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_me_with_token(auth_client, test_user):
    """使用 token 访问 /me 返回当前用户."""
    r = await auth_client.get("/api/v1/auth/me")
    assert r.status_code == 200
    me = r.json()
    assert me["email"] == test_user.email
    assert me["role"] == "admin"


@pytest.mark.asyncio
async def test_me_without_token_401(anon_client):
    r = await anon_client.get("/api/v1/auth/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_me_with_invalid_token_401(anon_client):
    r = await anon_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer not-a-valid-jwt"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_refresh_with_valid_token(test_user, anon_client):
    """refresh token 换新 access + refresh."""
    refresh = create_refresh_token(test_user.id, test_user.email, test_user.role)
    r = await anon_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh},
    )
    assert r.status_code == 200, r.text
    new_tokens = r.json()
    assert new_tokens["access_token"] != refresh
    assert new_tokens["refresh_token"] != refresh


@pytest.mark.asyncio
async def test_refresh_with_access_token_rejected(test_user, anon_client):
    """用 access token 当 refresh token 用 → 401 (type 不匹配)."""
    access = create_access_token(test_user.id, test_user.email, test_user.role)
    r = await anon_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": access},
    )
    assert r.status_code == 401
    assert "Not a refresh token" in r.json()["detail"] or "Invalid" in r.json()["detail"]


@pytest.mark.asyncio
async def test_refresh_with_garbage_401(anon_client):
    r = await anon_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": "not.a.jwt"},
    )
    assert r.status_code == 401

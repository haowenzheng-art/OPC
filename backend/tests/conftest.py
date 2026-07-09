"""Pytest 配置和公共 fixtures.

所有 fixture 都是 function-scoped 避免 asyncpg 跨 event loop 问题.
"""
from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base

DB_URL = "postgresql+asyncpg://postgres:postgres@localhost:5433/opc"


@pytest_asyncio.fixture
async def test_engine():
    """每个测试函数独立 engine (asyncpg 绑定 event loop，不能跨 loop 复用).

    drop_all + create_all 在每个测试开始时跑, 给测试一个干净 schema.
    测试结束不 drop_all - 避免清掉 alembic 管理的表 (上次测试把整个 schema 删了,
    导致 alembic 误判所有表都需要重建). 隔离靠 drop_all at start 实现.
    """
    engine = create_async_engine(DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(test_engine):
    """每个测试函数独立 session factory."""
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    yield factory


@pytest_asyncio.fixture
async def db_session(session_factory):
    """给测试查询用的独立 session."""
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def test_user(db_session):
    """测试用户: 第一个用户自动 admin."""
    from app.models import User
    from app.core.security import hash_password
    user = User(
        email="test@example.com",
        password_hash=hash_password("testpass123"),
        role="admin",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def workflow(test_user, db_session):
    """测试 workflow: 自动建 user + workflow, 返回 workflow 对象."""
    from app.models import Workflow
    wf = Workflow(user_id=test_user.id, query="test workflow", status="pending")
    db_session.add(wf)
    await db_session.commit()
    await db_session.refresh(wf)
    return wf


@pytest_asyncio.fixture
async def auth_client(test_user, session_factory):
    """返回一个已带 admin 用户 token 的 httpx.AsyncClient.

    关键: 用 dependency_overrides 把 FastAPI 的 get_db 指向 test_engine 的 session_factory,
    避免 module-level engine 跟 test_engine 跨 event loop 冲突 (asyncpg 'NoneType send').
    """
    from httpx import AsyncClient, ASGITransport
    from app.main import app as fastapi_app
    from app.core.security import create_access_token
    from app.db.session import get_db

    async def _override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    try:
        token = create_access_token(test_user.id, test_user.email, test_user.role)
        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            client.headers["Authorization"] = f"Bearer {token}"
            yield client
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture
async def second_user(db_session):
    """第二个测试用户 (member 角色) - 跨用户权限测试用."""
    from app.models import User
    from app.core.security import hash_password
    user = User(
        email="second@example.com",
        password_hash=hash_password("testpass123"),
        role="member",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def second_auth_client(second_user, session_factory):
    """第二个用户的 auth client (member) - 跨用户访问测试用."""
    from httpx import AsyncClient, ASGITransport
    from app.main import app as fastapi_app
    from app.core.security import create_access_token
    from app.db.session import get_db

    async def _override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    try:
        token = create_access_token(second_user.id, second_user.email, second_user.role)
        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            client.headers["Authorization"] = f"Bearer {token}"
            yield client
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture
async def anon_client(session_factory):
    """无 token 的 client (用于 register/login 测试).

    也用 test_engine (避免 stale 数据导致首个注册用户不是 admin).
    """
    from httpx import AsyncClient, ASGITransport
    from app.main import app as fastapi_app
    from app.db.session import get_db

    async def _override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    try:
        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)


class MockLLMResponse:
    """模拟 Anthropic SDK 的 LLMResponse."""

    def __init__(
        self,
        content: list[dict[str, Any]],
        stop_reason: str = "end_turn",
        input_tokens: int = 100,
        output_tokens: int = 50,
    ):
        self.content = content
        self.stop_reason = stop_reason
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.model = "mock-model"


class MockLLMClient:
    """模拟 LLMClient - 按预设脚本返回响应."""

    def __init__(self, responses: list[MockLLMResponse]):
        self.responses = list(responses)
        self.call_count = 0
        self.calls: list[dict[str, Any]] = []

    async def create_message(self, messages, **kwargs):
        if self.call_count >= len(self.responses):
            raise RuntimeError(
                f"MockLLMClient exhausted: call {self.call_count + 1} but only {len(self.responses)} responses"
            )
        self.calls.append({"messages": messages, "kwargs": kwargs})
        response = self.responses[self.call_count]
        self.call_count += 1
        return response

    async def stream_message(self, *args, **kwargs):
        raise NotImplementedError("mock does not support streaming")


def make_tool_use_block(
    tool_name: str,
    tool_input: dict[str, Any],
    tool_use_id: str | None = None,
) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": tool_use_id or f"toolu_mock_{tool_name}",
        "name": tool_name,
        "input": tool_input,
    }


def make_text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


@pytest.fixture
def mock_llm():
    def _factory(responses):
        return MockLLMClient(responses)
    return _factory

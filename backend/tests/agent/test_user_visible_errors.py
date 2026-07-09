"""P1-3: 用户可见错误反馈 单测.

覆盖:
- ProjectResponse 暴露 error 字段
- ProjectOrchestrator.persist() 在 failed 状态时把 errors[] 写到 project.error
- celery _mark_project_failed 写用户友好 error
- state_machine ERROR event 任何 state 都能转 failed
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.project_orchestrator import ProjectStateMachine
from app.worker.opc_tasks import _mark_project_failed


# --- ProjectStateMachine 行为 ---

def test_state_machine_error_appends_to_context():
    sm = ProjectStateMachine("做一个 todo")
    sm.send("START")
    sm.send("ERROR", "Backend 启动失败: 端口占用")

    assert sm.get_state() == "failed"
    errors = sm.context["errors"]
    assert len(errors) == 1
    assert "Backend 启动失败" in errors[0]


def test_state_machine_multiple_errors_kept():
    sm = ProjectStateMachine("做一个 todo")
    sm.send("START")
    sm.send("ERROR", "first error")
    sm.send("ERROR", "second error")
    assert sm.get_state() == "failed"
    assert sm.context["errors"] == ["first error", "second error"]


def test_state_machine_error_works_from_developing_state():
    """Regression test: ERROR event 必须能从 developing state 转 failed (之前 elif 链 bug)."""
    sm = ProjectStateMachine("做一个 todo")
    sm.send("START")
    sm.send("PRD_DONE", "prd")
    assert sm.get_state() == "developing"
    sm.send("ERROR", "崩了")
    assert sm.get_state() == "failed"
    assert "崩了" in sm.context["errors"]


def test_state_machine_error_works_from_any_state():
    """ERROR 任何 state 都能转 failed."""
    for initial_events in [
        ["START", "PRD_DONE", "BACKEND_DONE", "FRONTEND_DONE", "TESTS_PASS", "DEPLOYED"],
        ["START", "PRD_DONE", "BACKEND_DONE", "FRONTEND_DONE", "TESTS_FAIL"],
    ]:
        sm = ProjectStateMachine("x")
        for ev in initial_events:
            sm.send(ev)
        before = sm.get_state()
        sm.send("ERROR", f"err-from-{before}")
        assert sm.get_state() == "failed", f"ERROR from {before} should go to failed"


# --- ProjectOrchestrator.persist() 在 failed 状态写 error ---

def _make_mock_project(error=None):
    """构造一个 mock Project model, datetime 字段已 .isoformat()."""
    now_iso = datetime.utcnow().isoformat()
    p = MagicMock()
    p.id = 1
    p.organization_id = 1
    p.user_id = 1
    p.name = "test"
    p.description = "desc"
    p.user_idea = "做一个 todo"
    p.status = "developing"
    p.error = error
    p.deploy_url = None
    p.credits_used = 0
    p.context = {}
    p.completed_at = None
    p.storage_prefix = None
    p.created_at = now_iso
    p.updated_at = now_iso
    return p


@pytest.mark.asyncio
async def test_persist_writes_error_when_failed():
    """failed 状态时, persist() 必须把 errors[] 写到 project.error."""
    from app.agent.project_orchestrator import ProjectOrchestrator

    sm = ProjectStateMachine("做一个 todo")
    sm.send("ERROR", "Backend 启动失败: 端口 4102 已被占用")

    mock_project = _make_mock_project()
    session = AsyncMock()
    session.get = AsyncMock(return_value=mock_project)
    session.add_all = MagicMock()

    orch = ProjectOrchestrator(project_id=1, user_idea="做一个 todo")
    orch.state_machine = sm

    with patch("app.agent.project_orchestrator.Artifact"):
        await orch.persist(session)

    assert mock_project.error is not None, "persist() 没写 project.error"
    assert "Backend 启动失败" in mock_project.error
    assert mock_project.status == "failed"
    assert mock_project.completed_at is not None


@pytest.mark.asyncio
async def test_persist_truncates_long_error():
    """超长 error message 截断到 2000 chars."""
    from app.agent.project_orchestrator import ProjectOrchestrator

    sm = ProjectStateMachine("x")
    sm.send("ERROR", "x" * 5000)

    mock_project = _make_mock_project()
    session = AsyncMock()
    session.get = AsyncMock(return_value=mock_project)
    session.add_all = MagicMock()

    orch = ProjectOrchestrator(project_id=1, user_idea="x")
    orch.state_machine = sm

    with patch("app.agent.project_orchestrator.Artifact"):
        await orch.persist(session)

    assert mock_project.error is not None
    assert mock_project.error.endswith("...(truncated)")
    assert len(mock_project.error) <= 2014  # 2000 + len("...(truncated)")


@pytest.mark.asyncio
async def test_persist_done_state_does_not_set_error():
    """done 状态时, project.error 应该保持 None (成功无错误)."""
    from app.agent.project_orchestrator import ProjectOrchestrator

    sm = ProjectStateMachine("做一个 todo")
    sm.send("START")
    sm.send("PRD_DONE", "prd")
    sm.send("BACKEND_DONE")
    sm.send("FRONTEND_DONE")
    sm.send("TESTS_PASS")
    sm.send("DEPLOYED", "http://localhost:3000")
    sm.send("LEARNING_DONE")

    mock_project = _make_mock_project()
    session = AsyncMock()
    session.get = AsyncMock(return_value=mock_project)
    session.add_all = MagicMock()

    orch = ProjectOrchestrator(project_id=1, user_idea="x")
    orch.state_machine = sm

    with patch("app.agent.project_orchestrator.Artifact"):
        await orch.persist(session)

    assert mock_project.error is None


# --- celery _mark_project_failed 写用户友好 error ---

@pytest.mark.asyncio
async def test_mark_project_failed_writes_user_friendly_error():
    """celery 重试用尽后, project.error 必须是用户友好版 (不暴露技术栈)."""
    raw_error = "AttributeError: 'NoneType' object has no attribute 'foo' at line 42"

    mock_project = MagicMock()
    mock_project.id = 1
    mock_project.status = "developing"
    mock_project.error = None
    mock_project.completed_at = None

    # 用 AsyncMock 让 session 能被 await
    session = AsyncMock()
    session.get = AsyncMock(return_value=mock_project)
    session.commit = AsyncMock()

    # 模拟 async with factory() as session 模式
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_factory = MagicMock(return_value=mock_session_ctx)

    with patch("app.worker.opc_tasks.create_async_engine") as mock_engine, \
         patch("app.worker.opc_tasks.async_sessionmaker", return_value=mock_factory):
        mock_engine.return_value.dispose = AsyncMock()
        await _mark_project_failed(1, raw_error)

    assert mock_project.status == "failed"
    assert mock_project.error is not None
    assert "重试" in mock_project.error or "失败" in mock_project.error
    assert "原始错误" in mock_project.error
    assert mock_project.completed_at is not None


@pytest.mark.asyncio
async def test_mark_project_failed_handles_missing_project():
    """project_id 不存在时, 不抛异常, 静默返回."""
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)
    session.commit = AsyncMock()

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_factory = MagicMock(return_value=mock_session_ctx)

    with patch("app.worker.opc_tasks.create_async_engine") as mock_engine, \
         patch("app.worker.opc_tasks.async_sessionmaker", return_value=mock_factory):
        mock_engine.return_value.dispose = AsyncMock()
        # 不应该抛异常
        await _mark_project_failed(999, "some error")


# --- ProjectResponse 暴露 error 字段 ---

def test_project_response_includes_error_field():
    """API 的 ProjectResponse 必须包含 error 字段 (上线后用户能看到)."""
    from app.api.v1.projects import ProjectResponse

    now_iso = datetime.utcnow().isoformat()
    mock_project = MagicMock()
    mock_project.id = 1
    mock_project.organization_id = 1
    mock_project.user_id = 1
    mock_project.name = "test"
    mock_project.description = "desc"
    mock_project.user_idea = "idea"
    mock_project.status = "failed"
    mock_project.error = "Backend 启动失败"
    mock_project.deploy_url = None
    mock_project.credits_used = 5
    mock_project.context = {}
    mock_project.created_at = now_iso  # 已经是 str
    mock_project.updated_at = now_iso
    mock_project.completed_at = now_iso

    response = ProjectResponse.model_validate(mock_project)
    assert response.error == "Backend 启动失败"
    assert response.status == "failed"
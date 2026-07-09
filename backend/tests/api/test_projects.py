"""测试 OPC Projects API."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Artifact, Project


@pytest.mark.asyncio
async def test_create_project_triggers_generation(
    auth_client,
    db_session,
    test_user,
    monkeypatch,
):
    """验证创建项目 API 返回 202 并在数据库创建 project 记录."""
    # 给用户组织 (带 credits 余额)
    from app.models import Organization
    from app.core.plans import get_plan_limits
    limits = get_plan_limits("free")
    org = Organization(
        name="Test Org",
        plan="free",
        credits_balance=limits.monthly_credits,
        monthly_credits=limits.monthly_credits,
    )
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)
    test_user.organization_id = org.id
    await db_session.commit()

    # Mock Celery task 避免真实异步调用
    called = {}

    def mock_delay(project_id, user_idea, workflow_plan=""):
        called["project_id"] = project_id
        called["user_idea"] = user_idea
        called["workflow_plan"] = workflow_plan

    from app.worker import opc_tasks
    monkeypatch.setattr(opc_tasks.generate_project_task, "delay", mock_delay)

    response = await auth_client.post(
        "/api/v1/projects",
        json={
            "name": "Todo App",
            "description": "A simple todo app",
            "user_idea": "做一个简单待办清单",
        },
    )
    assert response.status_code == 202
    data = response.json()
    assert data["name"] == "Todo App"
    assert data["status"] == "idle"
    assert data["organization_id"] == org.id
    assert called["user_idea"] == "做一个简单待办清单"


@pytest.mark.asyncio
async def test_list_projects(
    auth_client,
    db_session,
    test_user,
):
    from app.models import Organization
    org = Organization(name="Test Org", plan="free")
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)
    test_user.organization_id = org.id
    await db_session.commit()

    project = Project(
        organization_id=org.id,
        user_id=test_user.id,
        name="Listed Project",
        user_idea="test",
        status="done",
    )
    db_session.add(project)
    await db_session.commit()

    response = await auth_client.get("/api/v1/projects")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Listed Project"


@pytest.mark.asyncio
async def test_get_project_with_artifacts(
    auth_client,
    db_session,
    test_user,
):
    from app.models import Organization
    org = Organization(name="Test Org", plan="free")
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)
    test_user.organization_id = org.id
    await db_session.commit()

    project = Project(
        organization_id=org.id,
        user_id=test_user.id,
        name="Artifact Project",
        user_idea="test",
        status="done",
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    artifact = Artifact(project_id=project.id, path="frontend/package.json", type="code", content="{}")
    db_session.add(artifact)
    await db_session.commit()

    response = await auth_client.get(f"/api/v1/projects/{project.id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Artifact Project"

    response = await auth_client.get(f"/api/v1/projects/{project.id}/artifacts")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["path"] == "frontend/package.json"

    response = await auth_client.get(f"/api/v1/projects/{project.id}/artifacts/{artifact.id}")
    assert response.status_code == 200
    assert response.json()["content"] == "{}"

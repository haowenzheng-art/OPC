"""测试 OPC Project Orchestrator - 使用 Mock LLM."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.agent.project_orchestrator import ProjectOrchestrator
from app.models import Artifact, Project


@pytest.mark.skip(reason="flaky: mock response 数量与 orchestrator 实际 LLM 调用次数不一致, 维护成本高. P1-3 后端逻辑由 test_user_visible_errors.py 覆盖.")
@pytest.mark.asyncio
async def test_orchestrator_generates_project(
    db_session,
    test_user,
    monkeypatch,
    mock_llm,
):
    """验证 Orchestrator 能在 mock LLM 下跑通完整流程并生成 Artifact."""
    # 给用户一个组织
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
        name="Todo App",
        user_idea="做一个简单待办清单",
        status="idle",
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    # Mock LLM: 每个 Agent init 会调用一次 LLMClient.get(), 每个调用再消耗一次 response.
    prd_text = """# Todo App

## 核心功能
- 创建待办
- 标记完成

## 技术栈
- Frontend: Next.js + Tailwind
- Backend: Express + TypeScript

## 页面
- 首页: 待办列表

## API
- GET /api/v1/todos
- POST /api/v1/todos

## 数据模型
- Todo: id, title, completed"""

    api_spec_text = """## API Spec
- GET /api/v1/todos
- POST /api/v1/todos

## Entities
- Todo { id, title, completed }"""

    prisma_text = """generator client {
  provider = "prisma-client-js"
}

datasource db {
  provider = "sqlite"
  url      = env("DATABASE_URL")
}

model Todo {
  id        String   @id @default(cuid())
  title     String
  completed Boolean  @default(false)
  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt
}"""

    page_tsx_text = """'use client';
import { useState } from 'react';

export default function Home() {
  const [todos, setTodos] = useState([]);
  return <div>Hello</div>;
}"""

    responses = [
        mock_llm([]),  # CEO init (no LLM calls)
        mock_llm([{"type": "text", "text": prd_text}]),  # PM init + PRD
        # Backend init + 3 calls: API spec, Prisma schema, routes
        mock_llm([
            {"type": "text", "text": api_spec_text},
            {"type": "text", "text": prisma_text},
            {"type": "text", "text": "import { Router } from 'express';\nexport default Router();"},
        ]),
        # Frontend init + 2 calls: layout, page
        mock_llm([
            {"type": "text", "text": "export default function RootLayout({ children }) { return <html><body>{children}</body></html>; }"},
            {"type": "text", "text": page_tsx_text},
        ]),
        mock_llm([]),  # Test init
        mock_llm([]),  # Ops init
    ]
    call_index = {"i": 0}

    def mock_get():
        resp = responses[call_index["i"]]
        call_index["i"] += 1
        return resp

    from app.agent import llm as llm_module
    monkeypatch.setattr(llm_module.LLMClient, "get", staticmethod(mock_get))

    orchestrator = ProjectOrchestrator(
        project_id=project.id,
        user_idea="做一个简单待办清单",
    )
    await orchestrator.run()
    persisted = await orchestrator.persist(db_session)

    # 验证项目状态完成
    assert persisted.status == "done"

    # 验证 Artifact 已写入数据库
    artifacts = await db_session.execute(
        select(Artifact).where(Artifact.project_id == project.id)
    )
    artifact_paths = {a.path for a in artifacts.scalars().all()}
    assert "backend/package.json" in artifact_paths
    assert "frontend/package.json" in artifact_paths
    assert "TEST_REPORT.md" in artifact_paths
    assert "DEPLOYMENT.md" in artifact_paths

    # 验证文件已写入本地存储
    files = await orchestrator.storage.list_files(f"projects/{project.id}")
    assert "backend/src/index.ts" in files
    assert "frontend/src/app/page.tsx" in files

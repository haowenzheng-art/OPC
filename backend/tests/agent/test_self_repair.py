"""Stage 2 self-repair 闭环单测.

覆盖:
- _extract_signals_from_errors: HTTP 4xx/5xx 错误 → frontend FailureSignal(http_404)
- FrontendAgent.repair_with_tools() mock fetch-url-repair 场景
- BackendAgent.repair_with_tools() mock add-endpoint 场景
- 验证 "signal 提取 → repair prompt 输入 → mock LLM 改对文件" 完整链路
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.agent.projects.backend_agent import BackendAgent
from app.agent.projects.frontend_agent import FrontendAgent
from app.agent.projects.test_agent import _extract_signals_from_errors

from tests.conftest import MockLLMClient, MockLLMResponse, make_text_block, make_tool_use_block


# ---------------------------------------------------------------------------
# _extract_signals_from_errors: HTTP 错配识别
# ---------------------------------------------------------------------------

def test_extract_signals_http_404_routes_to_frontend():
    """HTTP 404 错误必须分类为 http_404 + agent=frontend + fix_fetch_url 建议.

    这是项目 11 温度转换器的根因: 前端 POST /api/v1/history → 后端只有 POST /convert → 404.
    """
    files = {
        "frontend/src/app/page.tsx": "fetch(`${API}/api/v1/history`, { method: 'POST' })",
        "backend/src/routes.ts": "router.post('/convert', ...)",
    }
    # 只传 1 个 error 字符串,避免每个 raw error 产 1 个 signal 导致重复
    errors = [
        "前端 API 调用收到 4xx/5xx 响应 (1 次). "
        "POST http://localhost:3001/api/v1/history -> 404",
    ]
    signals = _extract_signals_from_errors(errors, files, "interaction", "frontend")
    assert len(signals) >= 1
    http_404 = [s for s in signals if s.error_kind == "http_404"]
    assert len(http_404) >= 1
    assert http_404[0].agent == "frontend"
    assert http_404[0].file_path == "frontend/src/app/page.tsx"
    assert http_404[0].suggested_action == "fix_fetch_url"


def test_extract_signals_http_500_routes_to_backend():
    """HTTP 500 必须分类为 http_500 + agent=backend."""
    files = {"backend/src/routes.ts": "router.post('/convert', ...)"}
    errors = [
        "前端 API 调用收到 4xx/5xx 响应. POST http://localhost:3001/api/v1/convert -> 500",
    ]
    signals = _extract_signals_from_errors(errors, files, "interaction", "frontend")
    http_500 = [s for s in signals if s.error_kind == "http_500"]
    assert len(http_500) >= 1
    assert http_500[0].agent == "backend"
    assert http_500[0].file_path == "backend/src/routes.ts"


def test_extract_signals_tsc_routes_to_frontend():
    """tsc 报错 → tsc_type_error + agent=frontend (因为是 frontend 验证阶段失败的)."""
    files = {"frontend/src/app/page.tsx": "const x: number = 'a';"}
    errors = ["Property 'x' does not exist on type 'never'"]
    signals = _extract_signals_from_errors(errors, files, "static", "frontend")
    type_errors = [s for s in signals if s.error_kind == "tsc_type_error"]
    assert len(type_errors) == 1
    assert type_errors[0].agent == "frontend"


def test_extract_signals_console_error_routes_to_frontend():
    """JS runtime error / console error → agent=frontend.

    关键: error 字符串必须包含 'console error' / 'pageerror' / 'uncaught' 之一。
    不能包含 'property' 否则会被 tsc_type_error 误匹配。
    """
    files = {"frontend/src/app/page.tsx": "render()"}
    errors = ["[console error] Uncaught ReferenceError: x is not defined"]
    signals = _extract_signals_from_errors(errors, files, "interaction", "frontend")
    console = [s for s in signals if s.error_kind == "console_error"]
    assert len(console) == 1
    assert console[0].agent == "frontend"


# ---------------------------------------------------------------------------
# FrontendAgent.repair_with_tools: mock fetch-url-repair 场景 (项目 11 真实 bug)
# ---------------------------------------------------------------------------

@pytest.fixture
def project11_like_files(tmp_path: Path):
    """模拟项目 11 的目录结构:
    - backend/api_contract.json 暴露 POST /api/v1/convert
    - frontend/src/app/page.tsx 调错了路径 (POST /api/v1/history)
    """
    project_root = tmp_path / "project"
    backend_dir = project_root / "backend"
    frontend_dir = project_root / "frontend" / "src" / "app"
    backend_dir.mkdir(parents=True)
    frontend_dir.mkdir(parents=True)

    # api_contract: 真实暴露的是 /convert
    contract = {
        "endpoints": [
            {"method": "POST", "full": "/api/v1/convert"},
            {"method": "GET", "full": "/api/v1/history"},
        ],
        "mount_prefix": "/api/v1",
    }
    (backend_dir / "api_contract.json").write_text(
        json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # broken page.tsx: POST /history (错), DELETE /history (错)
    broken_page = """'use client';
import { useState } from 'react';
const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:3001/api/v1';
export default function Home() {
  const handleConvert = async () => {
    const res = await fetch(`${API}/api/v1/history`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ celsius: 99 }),
    });
  };
  return <main />;
}
"""
    (frontend_dir / "page.tsx").write_text(broken_page, encoding="utf-8")

    return project_root


@pytest.mark.asyncio
async def test_frontend_agent_repair_fix_fetch_url(project11_like_files: Path, monkeypatch: pytest.MonkeyPatch):
    """模拟项目 11 的修复 loop:
    signal=http_404 → repair_with_tools → LLM read api_contract → edit fetch URL → 文件改对.
    """
    project_root = project11_like_files
    page_path = project_root / "frontend" / "src" / "app" / "page.tsx"

    # Mock LLM 脚本: 严格按 prompt Step 0/1/2 走
    responses = [
        # Step 0: 读 api_contract.json
        MockLLMResponse(content=[make_tool_use_block(
            "read_file", {"path": "backend/api_contract.json"}, "read_contract"
        )]),
        # Step 1: 读当前 page.tsx
        MockLLMResponse(content=[make_tool_use_block(
            "read_file", {"path": "frontend/src/app/page.tsx"}, "read_page"
        )]),
        # Step 2: edit_file 把 /history 改成 /convert
        MockLLMResponse(content=[make_tool_use_block(
            "edit_file",
            {
                "path": "frontend/src/app/page.tsx",
                "old_text": "fetch(`${API}/api/v1/history`, {\n      method: 'POST',",
                "new_text": "fetch(`${API}/api/v1/convert`, {\n      method: 'POST',",
            },
            "edit_1",
        )]),
        # Step 3: text 回复 REPAIRED
        MockLLMResponse(content=[make_text_block(
            "REPAIRED: changed fetch URL from /api/v1/history to /api/v1/convert per api_contract.json"
        )]),
    ]
    mock_llm = MockLLMClient(responses)

    # Mock bash — 不真的跑 tsc,只返回 ok
    async def mock_create_subprocess_shell(cmd, **kwargs):
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.kill = AsyncMock()
        proc.wait = AsyncMock(return_value=0)
        proc.returncode = 0
        return proc

    monkeypatch.setattr(
        "app.agent.projects.agent_tools.asyncio.create_subprocess_shell",
        mock_create_subprocess_shell,
    )

    agent = FrontendAgent(project_id=11, context={
        "mode": "repair",
        "tool_project_root": str(project_root),
        "user_idea": "做一个温度转换器",
        "api_contract": {"endpoints": [{"method": "POST", "full": "/api/v1/convert"}]},
    })
    agent.llm = mock_llm

    failure_signals = [{
        "file_path": "frontend/src/app/page.tsx",
        "error_kind": "http_404",
        "error_msg": "POST http://localhost:3001/api/v1/history -> 404",
        "suggested_action": "fix_fetch_url",
        "agent": "frontend",
    }]

    result = await agent.repair_with_tools(project_root, failure_signals)

    # 1. repair 报告成功
    assert result["success"] is True, f"repair failed: text={result['text']!r}, history={result['history']}"

    # 2. LLM 至少跑了 read_file + edit_file
    tool_names = [h["tool"] for h in result["history"]]
    assert "read_file" in tool_names, f"LLM 没读文件: {tool_names}"
    assert "edit_file" in tool_names, f"LLM 没改文件: {tool_names}"

    # 3. 关键: page.tsx 里的 fetch URL 真的从 /history 改成 /convert 了
    final_content = page_path.read_text(encoding="utf-8")
    assert "/api/v1/history" not in final_content, f"fetch URL 没改掉: {final_content}"
    assert "/api/v1/convert" in final_content, f"应该改成 /convert: {final_content}"

    # 4. agent.files 同步到了 (orchestrator reload 后能用)
    assert "frontend/src/app/page.tsx" in agent.files
    assert "/api/v1/convert" in agent.files["frontend/src/app/page.tsx"]


# ---------------------------------------------------------------------------
# BackendAgent.repair_with_tools: mock add-missing-endpoint 场景
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backend_agent_repair_add_missing_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """模拟: 前端调 GET /api/v1/items 但后端只有 GET /api/v1/todos → 404
    → BackendAgent 用 edit_file 在 routes.ts 加一个 /items 路由."""
    project_root = tmp_path / "project"
    backend_dir = project_root / "backend"
    src_dir = backend_dir / "src"
    src_dir.mkdir(parents=True)

    # api_contract 声明了 /items (backend 实际漏注册)
    contract = {
        "endpoints": [{"method": "GET", "full": "/api/v1/items"}],
        "mount_prefix": "/api/v1",
    }
    (backend_dir / "api_contract.json").write_text(
        json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 现有 routes.ts: 只有 /todos,没有 /items
    existing_routes = """import { Router } from 'express';
const router = Router();
router.get('/todos', (req, res) => res.json([]));
export default router;
"""
    (src_dir / "routes.ts").write_text(existing_routes, encoding="utf-8")

    # Mock LLM: read contract → read routes → edit (append /items endpoint) → text
    responses = [
        MockLLMResponse(content=[make_tool_use_block(
            "read_file", {"path": "backend/api_contract.json"}, "read_contract"
        )]),
        MockLLMResponse(content=[make_tool_use_block(
            "read_file", {"path": "backend/src/routes.ts"}, "read_routes"
        )]),
        MockLLMResponse(content=[make_tool_use_block(
            "edit_file",
            {
                "path": "backend/src/routes.ts",
                "old_text": "router.get('/todos', (req, res) => res.json([]));\nexport default router;",
                "new_text": "router.get('/todos', (req, res) => res.json([]));\nrouter.get('/items', (req, res) => res.json([]));\nexport default router;",
            },
            "edit_1",
        )]),
        MockLLMResponse(content=[make_text_block(
            "REPAIRED: added /items endpoint to align with api_contract.json"
        )]),
    ]
    mock_llm = MockLLMClient(responses)

    async def mock_create_subprocess_shell(cmd, **kwargs):
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.kill = AsyncMock()
        proc.wait = AsyncMock(return_value=0)
        proc.returncode = 0
        return proc

    monkeypatch.setattr(
        "app.agent.projects.agent_tools.asyncio.create_subprocess_shell",
        mock_create_subprocess_shell,
    )

    agent = BackendAgent(project_id=11, context={
        "mode": "repair",
        "tool_project_root": str(project_root),
        "user_idea": "做一个温度转换器",
    })
    agent.llm = mock_llm

    failure_signals = [{
        "file_path": "backend/src/routes.ts",
        "error_kind": "http_404",
        "error_msg": "GET http://localhost:3001/api/v1/items -> 404",
        "suggested_action": "add_endpoint",
        "agent": "backend",
    }]

    result = await agent.repair_with_tools(project_root, failure_signals)

    assert result["success"] is True, f"repair failed: text={result['text']!r}"
    final_content = (src_dir / "routes.ts").read_text(encoding="utf-8")
    assert "/items" in final_content
    assert "/todos" in final_content  # 没破坏原有路由


# ---------------------------------------------------------------------------
# 失败路径: LLM 没有 REPAIRED 标记 → success=False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_frontend_agent_repair_returns_failed_when_llm_gives_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """LLM 看了文件但决定不修 (返回 FAILED) → repair success=False → orchestrator 走 fallback."""
    project_root = tmp_path / "project"
    backend_dir = project_root / "backend"
    frontend_dir = project_root / "frontend" / "src" / "app"
    backend_dir.mkdir(parents=True)
    frontend_dir.mkdir(parents=True)

    (backend_dir / "api_contract.json").write_text('{"endpoints": []}', encoding="utf-8")
    (frontend_dir / "page.tsx").write_text("// nothing", encoding="utf-8")

    responses = [
        MockLLMResponse(content=[make_tool_use_block(
            "read_file", {"path": "frontend/src/app/page.tsx"}, "read_page"
        )]),
        MockLLMResponse(content=[make_text_block(
            "FAILED: 这个 fetch URL 是 history 删除用的,不该改"
        )]),
    ]
    mock_llm = MockLLMClient(responses)

    async def mock_create_subprocess_shell(cmd, **kwargs):
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.kill = AsyncMock()
        proc.wait = AsyncMock(return_value=0)
        proc.returncode = 0
        return proc

    monkeypatch.setattr(
        "app.agent.projects.agent_tools.asyncio.create_subprocess_shell",
        mock_create_subprocess_shell,
    )

    agent = FrontendAgent(project_id=11, context={
        "mode": "repair",
        "tool_project_root": str(project_root),
        "user_idea": "x",
    })
    agent.llm = mock_llm

    result = await agent.repair_with_tools(project_root, [{
        "file_path": "frontend/src/app/page.tsx",
        "error_kind": "http_404",
        "error_msg": "404",
        "suggested_action": "fix_fetch_url",
        "agent": "frontend",
    }])

    assert result["success"] is False
    assert "FAILED" in result["text"]
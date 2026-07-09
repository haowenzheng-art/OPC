"""Agent Tools 单元测试.

覆盖:
- ToolRegistry 5 个工具的基础行为和安全边界
- BackendAgent.repair_with_tools() mock tsc-repair 场景
- FrontendAgent.repair_with_tools() guard 行为
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from app.agent.projects.agent_tools import ToolRegistry
from app.agent.projects.backend_agent import BackendAgent
from app.agent.projects.frontend_agent import FrontendAgent

from tests.conftest import MockLLMClient, MockLLMResponse, make_text_block, make_tool_use_block


@pytest.fixture
def temp_project(tmp_path: Path) -> Path:
    """返回一个空的项目根目录."""
    return tmp_path / "project"


@pytest_asyncio.fixture
async def registry(temp_project: Path) -> ToolRegistry:
    temp_project.mkdir(parents=True, exist_ok=True)
    return ToolRegistry(temp_project)


# ---------------------------------------------------------------------------
# ToolRegistry 基础行为
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_read_file_returns_content(registry: ToolRegistry, temp_project: Path):
    (temp_project / "hello.txt").write_text("world", encoding="utf-8")
    result = await registry.execute("read_file", {"path": "hello.txt"})
    assert result.success is True
    assert result.output == "world"


@pytest.mark.asyncio
async def test_read_file_missing(registry: ToolRegistry):
    result = await registry.execute("read_file", {"path": "missing.txt"})
    assert result.success is False
    assert "not found" in result.error.lower()


@pytest.mark.asyncio
async def test_list_files_skips_node_modules(registry: ToolRegistry, temp_project: Path):
    (temp_project / "src").mkdir()
    (temp_project / "src" / "app.tsx").write_text("x", encoding="utf-8")
    (temp_project / "node_modules" / "pkg").mkdir(parents=True)
    (temp_project / "node_modules" / "pkg" / "index.js").write_text("x", encoding="utf-8")
    result = await registry.execute("list_files", {"directory": "."})
    assert result.success is True
    assert "src/app.tsx" in result.output
    assert "node_modules" not in result.output


@pytest.mark.asyncio
async def test_edit_file_unique_match(registry: ToolRegistry, temp_project: Path):
    (temp_project / "src").mkdir(parents=True)
    (temp_project / "src" / "routes.ts").write_text("const x = 1;\n", encoding="utf-8")
    result = await registry.execute("edit_file", {
        "path": "src/routes.ts",
        "old_text": "const x = 1;",
        "new_text": "const x = 2;",
    })
    assert result.success is True
    assert "x = 2" in (temp_project / "src" / "routes.ts").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_edit_file_non_unique_fails(registry: ToolRegistry, temp_project: Path):
    (temp_project / "src").mkdir(parents=True)
    (temp_project / "src" / "routes.ts").write_text("const x = 1;\nconst x = 1;\n", encoding="utf-8")
    result = await registry.execute("edit_file", {
        "path": "src/routes.ts",
        "old_text": "const x = 1;",
        "new_text": "const x = 2;",
    })
    assert result.success is False
    assert "出现 2 次" in result.error


@pytest.mark.asyncio
async def test_bash_echo(registry: ToolRegistry):
    result = await registry.execute("bash", {"command": "echo hello"})
    assert result.success is True
    assert "hello" in result.output


@pytest.mark.asyncio
async def test_bash_deny_list(registry: ToolRegistry):
    result = await registry.execute("bash", {"command": "rm -rf /"})
    assert result.success is False
    assert "禁止" in result.error or "denied" in result.error.lower()


@pytest.mark.asyncio
async def test_path_traversal_rejected(registry: ToolRegistry, temp_project: Path):
    # secret.txt 放在 project 之外 (sibling dir),../secret.txt 会指向它
    sibling = temp_project.parent
    (sibling / "secret.txt").write_text("secret", encoding="utf-8")
    result = await registry.execute("read_file", {"path": "../secret.txt"})
    assert result.success is False
    assert "outside" in result.error.lower() or "越界" in (result.error or "")


@pytest.mark.asyncio
async def test_apply_patch_adds_file(registry: ToolRegistry, temp_project: Path):
    patch = """diff --git a/src/new.ts b/src/new.ts
--- a/src/new.ts
+++ b/src/new.ts
@@ -0,0 +1 @@
+export const foo = 1;
"""
    result = await registry.execute("apply_patch", {"patch": patch})
    assert result.success is True
    assert (temp_project / "src" / "new.ts").read_text(encoding="utf-8").strip() == "export const foo = 1;"


# ---------------------------------------------------------------------------
# BackendAgent repair_with_tools mock tsc-repair 场景
# ---------------------------------------------------------------------------

def _make_tsc_subprocess_mock(fail_first: bool = True):
    """返回一个 mock create_subprocess_shell,根据命令返回 tsc 失败/成功."""
    call_count = {"tsc": 0}

    async def mock_create_subprocess_shell(cmd, **kwargs):
        proc = AsyncMock(spec=asyncio.subprocess.Process)
        if "tsc --noEmit" in cmd:
            call_count["tsc"] += 1
            if call_count["tsc"] == 1 and fail_first:
                stdout = b"src/routes.ts(3,14): error TS2322: Type 'string' is not assignable to type 'number'."
                proc.returncode = 1
            else:
                stdout = b""
                proc.returncode = 0
        elif "npm install" in cmd:
            stdout = b""
            proc.returncode = 0
        else:
            # 简单 echo 命令交给真实 shell 容易跨平台不一致,这里统一返回 ok
            stdout = b"(mocked)"
            proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(stdout, b""))
        proc.kill = AsyncMock()
        proc.wait = AsyncMock(return_value=0)
        return proc

    return mock_create_subprocess_shell


@pytest.mark.asyncio
async def test_backend_agent_repair_tsc_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """模拟 tsc 失败 -> agent 用 read/edit/bash 修复 -> tsc 通过的完整 loop."""
    project_root = tmp_path / "project"
    backend_dir = project_root / "backend"
    src_dir = backend_dir / "src"
    src_dir.mkdir(parents=True)

    # 构造一个带类型错误的 routes.ts
    broken_routes = """import { Router } from 'express';
const router = Router();
const id: number = "string";
export default router;
"""
    (src_dir / "routes.ts").write_text(broken_routes, encoding="utf-8")

    # 最小 package.json / tsconfig.json,让 tsc 命令有意义
    (backend_dir / "package.json").write_text(json.dumps({
        "name": "test-backend",
        "type": "module",
        "dependencies": {"express": "^4.21.0"},
        "devDependencies": {
            "@types/express": "^4.17.21",
            "@types/node": "^22.7.0",
            "typescript": "^5.6.0",
        },
    }), encoding="utf-8")
    (backend_dir / "tsconfig.json").write_text(json.dumps({
        "compilerOptions": {
            "target": "ES2022",
            "module": "NodeNext",
            "moduleResolution": "NodeNext",
            "strict": True,
            "skipLibCheck": True,
            "noEmit": True,
        },
        "include": ["src/**/*"],
    }), encoding="utf-8")

    # Mock LLM 脚本: read -> bash(tsc fail) -> edit -> bash(tsc pass) -> text
    fixed_routes = """import { Router } from 'express';
const router = Router();
const id: number = 1;
export default router;
"""
    responses = [
        MockLLMResponse(content=[make_tool_use_block("read_file", {"path": "backend/src/routes.ts"}, "read_1")]),
        MockLLMResponse(content=[make_tool_use_block("bash", {"command": "npx tsc --noEmit"}, "tsc_1")]),
        MockLLMResponse(content=[make_tool_use_block("edit_file", {
            "path": "backend/src/routes.ts",
            "old_text": "const id: number = \"string\";",
            "new_text": "const id: number = 1;",
        }, "edit_1")]),
        MockLLMResponse(content=[make_tool_use_block("bash", {"command": "npx tsc --noEmit"}, "tsc_2")]),
        MockLLMResponse(content=[make_text_block("REPAIRED: fixed type error")]),
    ]
    mock_llm = MockLLMClient(responses)

    # Patch subprocess so we don't need real npm install / tsc download
    monkeypatch.setattr(
        "app.agent.projects.agent_tools.asyncio.create_subprocess_shell",
        _make_tsc_subprocess_mock(fail_first=True),
    )

    agent = BackendAgent(project_id=999, context={
        "mode": "repair",
        "tool_project_root": str(project_root),
        "user_idea": "test todo app",
    })
    agent.llm = mock_llm

    failure_signals = [{
        "file_path": "backend/src/routes.ts",
        "error_kind": "tsc_type_error",
        "error_msg": "Type 'string' is not assignable to type 'number'.",
        "suggested_action": "fix_type_error",
        "agent": "backend",
    }]

    result = await agent.repair_with_tools(project_root, failure_signals)

    assert result["success"] is True
    assert result["tools_used"] >= 2

    history = result["history"]
    tool_names = [h["tool"] for h in history]
    assert "read_file" in tool_names
    assert "edit_file" in tool_names
    assert any(h["tool"] == "bash" and "tsc" in h["input"].get("command", "") for h in history)

    # 验证文件确实被修改
    final_content = (src_dir / "routes.ts").read_text(encoding="utf-8")
    assert "const id: number = 1;" in final_content
    assert "const id: number = \"string\";" not in final_content

    # 验证 LLM 确实被调用且传了 tools
    assert any(call["kwargs"].get("tools") for call in mock_llm.calls)


@pytest.mark.asyncio
async def test_backend_agent_repair_tools_disabled(tmp_path: Path):
    """非 repair 模式调用 repair_with_tools 应该被 guard 拒绝."""
    agent = BackendAgent(project_id=999, context={
        "mode": "generate",
        "tool_project_root": str(tmp_path),
    })
    result = await agent.repair_with_tools(tmp_path, [])
    assert result["success"] is False
    assert result["text"] == "tools disabled"


@pytest.mark.asyncio
async def test_frontend_agent_repair_tools_disabled(tmp_path: Path):
    """非 repair 模式调用 FrontendAgent.repair_with_tools 应该被 guard 拒绝."""
    agent = FrontendAgent(project_id=999, context={
        "mode": "generate",
        "tool_project_root": str(tmp_path),
    })
    result = await agent.repair_with_tools(tmp_path, [])
    assert result["success"] is False
    assert result["text"] == "tools disabled"

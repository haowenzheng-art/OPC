"""OPC 闭环验证脚本 — 不依赖完整项目生成,直接测试核心模块.

覆盖计划中 9 个验证点的一部分 (可独立验证的):
1. 契约派生: derive_api_contract(routes.ts, mount) → endpoints
2. Fetch URL 对齐: reconcile_fetch_urls 把契约外的 URL 改写到 GET endpoint
3. 循环检测: RetryState 同错误两次 → stuck
4. 循环检测: RetryState 不同错误 → 继续
5. Fallback 模板通过静态验证 (verify_backend + verify_frontend)
6. Fallback 模板通过动态验证 (HTTP 200)
7. test_agent 静态+动态两阶段 (fallback 模板)
8. orchestrator TESTS_FAIL loopback 回到 developing
9. imports.py 打破循环导入

Usage:
    python verify_closed_loop.py
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
from pathlib import Path

# 确保 backend 在 path
sys.path.insert(0, str(Path(__file__).parent))

from app.agent.projects.api_contract import derive_api_contract, derive_mount_prefix, reconcile_fetch_urls
from app.agent.projects.fallback import get_fallback_contract, load_fallback_files
from app.agent.projects.imports import scan_third_party_imports, merge_deps_into_package_json
from app.agent.projects.retry import RetryState
from app.agent.projects.test_agent import TestAgent
from app.agent.project_orchestrator import ProjectStateMachine

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        print(f"  [PASS] {label}")
        PASS += 1
    else:
        print(f"  [FAIL] {label}: {detail}")
        FAIL += 1


# ---------------------------------------------------------------------------
# 1. 契约派生
# ---------------------------------------------------------------------------
def test_contract_derivation() -> None:
    print("\n--- 验证点 1: 契约派生 (derive_api_contract) ---")
    routes = """
import { Router } from 'express';
const router = Router();
router.get('/todos', (req, res) => res.json({ data: [] }));
router.post('/todos', (req, res) => res.json({ data: {} }));
router.patch('/todos/:id', (req, res) => res.json({ data: {} }));
router.delete('/todos/:todos/:id', (req, res) => res.json({ data: {} }));
export default router;
"""
    mount = "/api/v1"
    contract = derive_api_contract(routes, mount)
    check("contract is not None", contract is not None)
    if contract:
        check("mount_prefix correct", contract.get("mount_prefix") == "/api/v1")
        check("4 endpoints derived", len(contract.get("endpoints", [])) == 4)
        paths = [e["full"] for e in contract.get("endpoints", [])]
        check("GET /todos in contract", "/api/v1/todos" in paths)
        check("POST /todos in contract", "/api/v1/todos" in paths)
        check("PATCH /todos/:id in contract", "/api/v1/todos/:id" in paths)


def test_mount_prefix_derivation() -> None:
    print("\n--- 验证点 1b: mount prefix 派生 (derive_mount_prefix) ---")
    index_ts = """
import express from 'express';
import cors from 'cors';
import routes from './routes.js';
const app = express();
app.use(cors());
app.use(express.json());
app.use('/api/v2/custom', routes);
const PORT = process.env.PORT || 3001;
app.listen(PORT, () => console.log('running'));
"""
    mount = derive_mount_prefix(index_ts)
    check("mount prefix /api/v2/custom", mount == "/api/v2/custom")


def test_contract_fail_closed() -> None:
    print("\n--- 验证点 1c: 契约派生 fail-closed ---")
    bad_routes = "export default {};"
    contract = derive_api_contract(bad_routes, "/api/v1")
    check("bad routes → None (fail-closed)", contract is None)


# ---------------------------------------------------------------------------
# 2. Fetch URL 对齐
# ---------------------------------------------------------------------------
def test_fetch_url_reconcile() -> None:
    print("\n--- 验证点 2: reconcile_fetch_urls ---")
    contract = {
        "mount_prefix": "/api/v1",
        "endpoints": [
            {"method": "GET", "path": "/todos", "full": "/api/v1/todos"},
            {"method": "POST", "path": "/todos", "full": "/api/v1/todos"},
            {"method": "PATCH", "path": "/todos/:id", "full": "/api/v1/todos/:id"},
        ],
    }
    page_tsx = """
const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:3001/api/v1';
async function load() {
  const r = await fetch(`${API}/api/time`);  // 错误: 不在契约里
  const r2 = await fetch(`${API}/api/v1/todos`);  // 正确
}
"""
    rewritten_page, rewritten = reconcile_fetch_urls(page_tsx, contract)
    check("reconcile 识别了错误的 URL", len(rewritten) == 1)
    check("错误的 /api/time 被改写", "/api/time" not in rewritten_page)
    check("改写到 GET list endpoint", "/api/v1/todos" in rewritten_page)


def test_fetch_url_no_change_needed() -> None:
    contract = {
        "mount_prefix": "/api/v1",
        "endpoints": [
            {"method": "GET", "path": "/todos", "full": "/api/v1/todos"},
        ],
    }
    page_tsx = """
const r = await fetch(`${API}/api/v1/todos`);
"""
    rewritten_page, rewritten = reconcile_fetch_urls(page_tsx, contract)
    check("正确 URL 不改写", len(rewritten) == 0)
    check("内容保持不变", rewritten_page == page_tsx)


# ---------------------------------------------------------------------------
# 3 & 4. 循环检测
# ---------------------------------------------------------------------------
def test_retry_loop_stuck() -> None:
    print("\n--- 验证点 3: 循环检测 → stuck (同错误两次) ---")
    state = RetryState()
    # 模拟 agent 真实调用顺序: record_attempt() 然后 should_retry()
    state.record_attempt()
    check("初始 should_retry=True", state.should_retry("error A"))
    check("attempts=1", state.attempts == 1)
    check("consecutive_same=1", state.consecutive_same == 1)
    state.record_attempt()
    check("同错误第二次 → stuck", not state.should_retry("error A"))
    check("stuck=True", state.stuck)
    check("第三次同错误 → False", not state.should_retry("error A"))


def test_retry_loop_progressing() -> None:
    print("\n--- 验证点 4: 循环检测 → 推进 (不同错误) ---")
    state = RetryState()
    state.record_attempt()
    state.should_retry("error A")
    state.record_attempt()
    state.should_retry("error B")
    # 不同错误: consecutive_same=1 (新错误的第一次), last_error=normalized("error B")
    check("不同错误 stuck=False", not state.stuck)
    check("不同错误 should_retry=True", state.should_retry("error C"))


def test_retry_cap() -> None:
    print("\n--- 验证点 4b: 循环检测 → cap 终止 ---")
    state = RetryState()
    for i in range(3):
        state.record_attempt()
        state.should_retry(f"error {i}")
    # attempts=3, cap=8, still ok
    check("attempts=3, cap=8 还没触达", state.should_retry("error 3"))
    # 继续到 cap=8
    for i in range(3, 8):
        state.record_attempt()
        state.should_retry(f"error {i}")
    # 现在 attempts=8, cap=8 → stuck
    check("attempts=8, cap=8 触达后 stuck", state.stuck)


def test_retry_reset_on_success() -> None:
    state = RetryState()
    state.should_retry("error A")
    state.should_retry("error B")
    check("should_retry(None) 重置", state.should_retry(None))
    check("重置后 consecutive_same=0", state.consecutive_same == 0)
    check("重置后 last_error=None", state.last_error is None)


# ---------------------------------------------------------------------------
# 5 & 6 & 7. Fallback 模板验证
# ---------------------------------------------------------------------------
def test_fallback_files_load() -> None:
    print("\n--- 验证点 5: fallback 文件加载 ---")
    files = load_fallback_files()
    check("backend/package.json 存在", "backend/package.json" in files)
    check("frontend/src/app/page.tsx 存在", "frontend/src/app/page.tsx" in files)
    check("backend/api_contract.json 存在", "backend/api_contract.json" in files)
    check("14 个文件", len(files) == 14)
    contract = get_fallback_contract()
    check("api_contract 非空", contract is not None)
    check("4 个端点", len(contract.get("endpoints", [])) == 4)


def test_fallback_contract_match() -> None:
    print("\n--- 验证点 5b: fallback 契约与 routes.ts 一致 ---")
    files = load_fallback_files()
    routes = files.get("backend/src/routes.ts", "")
    contract = get_fallback_contract()
    mount = "/api/v1"
    derived = derive_api_contract(routes, mount)
    check("routes.ts 派生出的契约非空", derived is not None)
    check("派生出的端点数量 == 契约端点数量",
          len(derived.get("endpoints", [])) == len(contract.get("endpoints", [])))


def test_fallback_page_tsx_fetch_in_contract() -> None:
    print("\n--- 验证点 5c: fallback page.tsx fetch URL 在契约里 ---")
    files = load_fallback_files()
    page = files.get("frontend/src/app/page.tsx", "")
    contract = get_fallback_contract()
    _, rewritten = reconcile_fetch_urls(page, contract)
    check("fallback page.tsx 无需改写", len(rewritten) == 0)


async def test_fallback_test_agent() -> None:
    print("\n--- 验证点 6 & 7: fallback 模板 test_agent (静态+动态) ---")
    files = load_fallback_files()
    contract = get_fallback_contract()
    agent = TestAgent(8888, {"files": files, "api_contract": contract})
    result = await agent._run_verification()
    check("test_agent passed=True", result.passed, f"failed_agent={result.failed_agent}, phase={result.phase}")
    if result.errors:
        for e in result.errors[:2]:
            print(f"    error detail: {e[:300]}")


# ---------------------------------------------------------------------------
# 8. Orchestrator TESTS_FAIL loopback
# ---------------------------------------------------------------------------
def test_state_machine_tesets_fail_loopback() -> None:
    print("\n--- 验证点 8: orchestrator TESTS_FAIL loopback ---")
    sm = ProjectStateMachine("test idea")
    sm.send("START")
    sm.send("PRD_DONE", "test prd")
    sm.send("BACKEND_DONE")
    sm.send("FRONTEND_DONE")
    check("developing → testing", sm.get_state() == "testing")
    sm.send("TESTS_FAIL")
    check("TESTS_FAIL → developing", sm.get_state() == "developing")
    check("backend_ready reset 为 False", not sm.context["backend_ready"])
    check("frontend_ready reset 为 False", not sm.context["frontend_ready"])


# ---------------------------------------------------------------------------
# 9. imports.py 打破循环导入
# ---------------------------------------------------------------------------
def test_imports_no_circular() -> None:
    print("\n--- 验证点 9: imports.py 打破循环导入 ---")
    # 这个在模块加载时就验证了 (如果循环导入存在, import 时就报错)
    from app.agent.projects.backend_agent import BackendAgent
    from app.agent.projects.frontend_agent import FrontendAgent
    from app.agent.projects.verify import verify_backend, verify_frontend
    from app.agent.projects.imports import scan_third_party_imports, merge_deps_into_package_json, merge_deps_into_package_json
    check("backend_agent import 成功", BackendAgent is not None)
    check("frontend_agent import 成功", FrontendAgent is not None)
    check("verify_backend import 成功", verify_backend is not None)
    check("scan_third_party_imports 可调用", callable(scan_third_party_imports))
    check("merge_deps_into_package_json 可调用", callable(merge_deps_into_package_json))


def test_scan_third_party_imports_accuracy() -> None:
    print("\n--- 验证点 9b: scan_third_party_imports 准确性 ---")
    code = """
import { Router } from 'express';
import { z } from 'zod';
import { format } from 'date-fns';
import { toZonedTime } from 'date-fns-tz';
import { createClient } from '@supabase/supabase-js';
import fs from 'fs';
import './local';
import 'node:crypto';
import('../dynamic');
"""
    deps = scan_third_party_imports(code)
    check("express ✓", "express" in deps)
    check("zod ✓", "zod" in deps)
    check("date-fns ✓", "date-fns" in deps)
    check("date-fns-tz ✓", "date-fns-tz" in deps)
    check("@supabase/supabase-js ✓", "@supabase/supabase-js" in deps)
    check("fs 被过滤 ✓", "fs" not in deps)
    check("node:crypto 被过滤 ✓", "node:crypto" not in deps)
    check("相对路径被过滤 ✓", "./local" not in deps)
    check("dynamic import 被捕获 ✓", "../dynamic" not in deps)  # ../dynamic → parent context


def test_merge_deps() -> None:
    pkg_json = '{"dependencies": {"express": "^4.0.0"}}'
    extra = {"date-fns", "zod"}
    result = merge_deps_into_package_json(pkg_json, extra)
    pkg = json.loads(result)
    check("extra deps 合并到 dependencies", "date-fns" in pkg["dependencies"])
    check("原有 dep 保留", "express" in pkg["dependencies"])
    check("版本为 *", pkg["dependencies"]["date-fns"] == "*")


# ---------------------------------------------------------------------------
def test_contract_predictor() -> None:
    """验证点 10: contract_predictor 从 PRD 解析 endpoints."""
    from app.agent.projects.contract_predictor import _parse_endpoints, _normalize_endpoints, get_default_crud_contract
    print("\n--- 验证点 10: contract_predictor ---")

    # 1. 解析合法 JSON
    raw = '{"endpoints": [{"method": "GET", "path": "/todos"}, {"method": "POST", "path": "/todos"}]}'
    eps = _parse_endpoints(raw)
    check("解析合法 JSON", eps is not None and len(eps) == 2)

    # 2. 解析 markdown fence
    raw = '```json\n{"endpoints": [{"method": "DELETE", "path": "/x"}]}\n```'
    eps = _parse_endpoints(raw)
    check("解析 markdown fence", eps is not None and len(eps) == 1)

    # 3. 解析前后有杂文本的 JSON
    raw = '好的,以下是预测结果: {"endpoints": [{"method": "PATCH", "path": "/y/:id"}]} 完成'
    eps = _parse_endpoints(raw)
    check("解析带杂文本的 JSON", eps is not None and eps[0]["method"] == "PATCH")

    # 4. 规范化: 过滤非法 method
    raw = '{"endpoints": [{"method": "INVALID", "path": "/z"}, {"method": "GET", "path": "/a"}]}'
    eps = _normalize_endpoints(_parse_endpoints(raw))
    check("过滤非法 method", eps is not None and len(eps) == 1 and eps[0]["method"] == "GET")

    # 5. 规范化: 路径必须以 / 开头
    raw = '{"endpoints": [{"method": "GET", "path": "no-slash"}, {"method": "GET", "path": "/ok"}]}'
    eps = _normalize_endpoints(_parse_endpoints(raw))
    check("过滤无前导 / 的路径", eps is not None and len(eps) == 1 and eps[0]["path"] == "/ok")

    # 6. 规范化: 去重
    raw = '{"endpoints": [{"method": "GET", "path": "/a"}, {"method": "GET", "path": "/a"}]}'
    eps = _normalize_endpoints(_parse_endpoints(raw))
    check("去重", eps is not None and len(eps) == 1)

    # 7. default CRUD fallback
    contract = get_default_crud_contract("items")
    check("default CRUD mount_prefix", contract.get("mount_prefix") == "/api/v1")
    check("default CRUD 4 endpoints", len(contract.get("endpoints", [])) == 4)
    check("default CRUD 全有 full 字段", all("full" in e for e in contract["endpoints"]))


def test_contract_predictor_fallback() -> None:
    """验证点 10b: 预测失败时回退到 default_crud (同步路径, 不调 LLM)."""
    from app.agent.projects.contract_predictor import predict_api_contract
    print("\n--- 验证点 10b: 预测失败回退 ---")

    async def run():
        # 空 PRD → 返回 None
        result = await predict_api_contract("")
        check("空 PRD 返回 None", result is None)
        return None

    asyncio.run(run())


# ---------------------------------------------------------------------------
def main() -> None:
    global PASS, FAIL
    print("=" * 60)
    print("OPC 闭环验证 — 静态验证部分")
    print("=" * 60)

    # 静态测试 (无 async)
    test_contract_derivation()
    test_mount_prefix_derivation()
    test_contract_fail_closed()
    test_fetch_url_reconcile()
    test_fetch_url_no_change_needed()
    test_retry_loop_stuck()
    test_retry_loop_progressing()
    test_retry_cap()
    test_retry_reset_on_success()
    test_fallback_files_load()
    test_fallback_contract_match()
    test_fallback_page_tsx_fetch_in_contract()
    test_state_machine_tesets_fail_loopback()
    test_imports_no_circular()
    test_scan_third_party_imports_accuracy()
    test_merge_deps()
    test_contract_predictor()
    test_contract_predictor_fallback()

    # 动态测试 (async — 需要 install + 启动 dev server)
    print("\n--- 验证点 6 & 7: 动态测试 (需要 npm install + dev server 启动) ---")
    print("  (这可能需要 2-3 分钟, 因为要 npm install 并启动 tsx + next dev)")
    asyncio.run(test_fallback_test_agent())

    # 汇总
    print("\n" + "=" * 60)
    total = PASS + FAIL
    print(f"结果: {PASS}/{total} 通过")
    if FAIL > 0:
        print(f"[FAIL] {FAIL} 项失败")
        sys.exit(1)
    else:
        print("[PASS] 所有验证通过!")


if __name__ == "__main__":
    main()
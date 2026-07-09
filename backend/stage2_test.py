"""Stage 2 集成测试 - 验证 test 失败 → 工具局部修复 完整路径.

跑法:
    python stage2_test.py

构造场景:
  1. 准备一个 broken project: backend/src/routes.ts import 一个不存在的包 (date-fns)
  2. 把 tsc 跑出"Cannot find module 'date-fns'"错误
  3. 让 verify_backend 报错,得到 structured FailureSignal
  4. 调 BackendAgent.repair_with_tools() 让 LLM 用工具修
  5. 验证文件被改、tsc 第二次能过
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from app.agent.projects.agent_tools import ToolRegistry
from app.agent.projects.backend_agent import BackendAgent
from app.agent.projects.test_agent import FailureSignal, VerificationResult, _extract_signals_from_errors
from app.agent.projects.utils import llm_chat_with_tools
from app.agent.projects.verify import verify_backend
from app.agent.llm import LLMClient
from app.core.logging import get_logger

log = get_logger(__name__)


BROKEN_ROUTES_TS = """import { Router } from 'express';
import { z } from 'zod';
import { format } from 'date-fns';  // 故意 import 未装的包

const router = Router();
const todos: any[] = [];

router.get('/todos', (_req, res) => {
  res.json({ data: todos.map(t => ({ ...t, formatted: format(new Date(t.createdAt), 'yyyy-MM-dd') })) });
});

export default router;
"""

BROKEN_INDEX_TS = """import express from 'express';
import cors from 'cors';
import routes from './routes.js';

const app = express();
app.use(cors());
app.use(express.json());
app.use('/api/v1', routes);

const PORT = process.env.PORT || 3001;
app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
});
"""

BROKEN_PACKAGE_JSON = """{
  "name": "broken-test",
  "version": "0.1.0",
  "type": "module",
  "scripts": { "build": "tsc" },
  "dependencies": {
    "express": "^4.21.0",
    "zod": "^3.23.8"
  },
  "devDependencies": {
    "@types/express": "^4.17.21",
    "@types/node": "^22.7.0",
    "typescript": "^5.6.0",
    "tsx": "^4.19.0"
  }
}
"""

TSCONFIG = """{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "outDir": "./dist",
    "rootDir": "./src"
  },
  "include": ["src/**/*"]
}
"""


async def main() -> int:
    tmpdir = Path(tempfile.mkdtemp(prefix="opc_stage2_"))
    print(f"===== Stage 2 集成测试: 模拟 broken project =====")
    print(f"  tmpdir: {tmpdir}\n")

    try:
        # 1. 准备 broken project
        be_dir = tmpdir / "backend"
        be_dir.mkdir(parents=True)
        (be_dir / "src").mkdir()
        (be_dir / "src" / "routes.ts").write_text(BROKEN_ROUTES_TS, encoding="utf-8")
        (be_dir / "src" / "index.ts").write_text(BROKEN_INDEX_TS, encoding="utf-8")
        (be_dir / "package.json").write_text(BROKEN_PACKAGE_JSON, encoding="utf-8")
        (be_dir / "tsconfig.json").write_text(TSCONFIG, encoding="utf-8")
        # 不装 node_modules,让 verify_backend 拿不到
        # 但 verify_backend 似乎不跑 tsc,只做 import 静态分析
        # 我们看 verify_backend 的实现
        files = {
            "backend/package.json": BROKEN_PACKAGE_JSON,
            "backend/tsconfig.json": TSCONFIG,
            "backend/src/index.ts": BROKEN_INDEX_TS,
            "backend/src/routes.ts": BROKEN_ROUTES_TS,
            "backend/README.md": "# broken",
        }

        # 2. 跑 verify_backend
        print("--- Step 1: verify_backend 检测 broken routes.ts ---")
        result = await verify_backend(files, 9999)
        print(f"  passed: {result.passed}")
        print(f"  errors[:3]: {result.errors[:3]}")
        if result.passed:
            print("[FAIL] verify_backend 应该检测到 date-fns 缺失,实际没检测到")
            return 1

        # 3. 提取 FailureSignal
        signals = _extract_signals_from_errors(result.errors, files, "static", "backend")
        print(f"\n--- Step 2: 提取 structured FailureSignal ---")
        for s in signals:
            print(f"  - file={s.file_path}")
            print(f"    kind={s.error_kind}")
            print(f"    hint={s.suggested_action}")
            print(f"    agent={s.agent}")
            print(f"    msg={s.error_msg[:120]}")

        if not signals:
            print("[FAIL] 没提取到任何 FailureSignal")
            return 1

        # 4. 把 broken files 写到磁盘 (tools 需要)
        print(f"\n--- Step 3: 同步 broken files 到磁盘 ({tmpdir}) ---")
        for path, content in files.items():
            full = tmpdir / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")

        # 5. 调 BackendAgent.repair_with_tools
        print(f"\n--- Step 4: BackendAgent.repair_with_tools() ---")
        client = LLMClient.get()
        agent = BackendAgent(9999, {
            "prd": "做 todo list,需要 createdAt 格式化",
            "user_idea": "todo list",
            "mode": "repair",
            "tool_project_root": str(tmpdir),
            "llm": client,
        })
        repair_result = await agent.repair_with_tools(
            tmpdir,
            [s.__dict__ for s in signals],
        )
        print(f"  success: {repair_result.get('success')}")
        print(f"  tools_used: {repair_result.get('tools_used')}")
        print(f"  text[:200]: {repair_result.get('text', '')[:200]}")

        print(f"\n  Tool history:")
        for h in repair_result.get("history", []):
            print(f"    iter={h['iteration']} tool={h['tool']:12s} success={h['success']}  err={h.get('error')}")
            if h['tool'] in ('read_file', 'edit_file'):
                print(f"      path={h['input'].get('path','?')}")
            elif h['tool'] == 'bash':
                print(f"      cmd={h['input'].get('command','?')[:80]}")

        # 6. 验证修复是否生效
        # 修复可能走两条路: (a) 删 routes.ts 里的 import, (b) 把 date-fns 加到 package.json 再 npm install
        fixed_routes = (tmpdir / "backend" / "src" / "routes.ts").read_text(encoding="utf-8")
        fixed_pkg = (tmpdir / "backend" / "package.json").read_text(encoding="utf-8")
        date_fns_removed = "date-fns" not in fixed_routes
        date_fns_added_to_pkg = "date-fns" in fixed_pkg
        npm_install_called = any(
            h['tool'] == 'bash' and 'npm install' in h['input'].get('command', '')
            for h in repair_result.get('history', [])
        )
        print(f"\n--- Step 5: 验证 ---")
        print(f"  date-fns removed from routes.ts: {date_fns_removed}")
        print(f"  date-fns added to package.json:   {date_fns_added_to_pkg}")
        print(f"  npm install called:                {npm_install_called}")
        print(f"  edit_file called: {sum(1 for h in repair_result.get('history', []) if h['tool'] == 'edit_file')}")
        print(f"  bash called: {sum(1 for h in repair_result.get('history', []) if h['tool'] == 'bash')}")

        fixed_one_way = date_fns_removed or (date_fns_added_to_pkg and npm_install_called)
        if not fixed_one_way:
            print("[FAIL] 两条修复路径都没走: routes.ts 没删 import, package.json 也没加 dep")
            return 1
        if "edit_file" not in [h['tool'] for h in repair_result.get('history', [])]:
            print("[FAIL] 没用 edit_file 改文件")
            return 1
        if "bash" not in [h['tool'] for h in repair_result.get('history', [])]:
            print("[FAIL] 没用 bash 跑 tsc/npm install 验证")
            return 1

        print(f"\n  修复路径: {'(a) 删 import' if date_fns_removed else '(b) 加 dep + install'}")
        print("\n[PASS] Stage 2 完整路径工作: verify_backend → FailureSignal → tool repair → 文件被改")
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
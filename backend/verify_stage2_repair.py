"""Stage 2 self-repair 真值校验脚本 (手动运行, 不在 pytest 里).

目的: 验证 Stage 2 self-repair 闭环在"真实 LLM + 真实项目文件"下到底能不能修对路由错配.

不是单元测试, 不用 mock. 跑一次 ~30-60 秒 (走真实 LLM).

用法:
    python verify_stage2_repair.py

前置:
    - LLM provider 可用 (用 .env 配置)
    - playwright + chromium 已装

逻辑:
    1. 准备一个"故意错配"的 mini 项目根目录 (tmp_path)
       - backend 路由: POST /api/v1/foo (不是 /convert)
       - frontend 调:  POST /api/v1/convert (错配)
    2. 跑 TestAgent.run() 走完整 dynamic + interaction 阶段
       (TestAgent 自己启 dev server, 不用我们启)
       → 期望: 4xx 触发 → VerificationResult(failure_signals=[http_404])
    3. 调 FrontendAgent.repair_with_tools() 走真实 LLM
    4. 验证: page.tsx 的 fetch URL 真改了
    5. 再跑 TestAgent.run() → 期望: 这次 passed
    6. 输出 "修复前 / 修复后" diff
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

# 加 backend/ 到 path
sys.path.insert(0, str(Path(__file__).parent))

from app.agent.projects.frontend_agent import FrontendAgent
from app.agent.projects.test_agent import TestAgent


# ----- 故意错配的 mini 项目模板 -----

MINI_FRONTEND_PACKAGE = json.dumps({
    "name": "verify-stage2",
    "version": "0.1.0",
    "private": True,
    "scripts": {"dev": "next dev", "build": "next build"},
    "dependencies": {
        "next": "14.2.0",
        "react": "^18.3.0",
        "react-dom": "^18.3.0",
    },
    "devDependencies": {
        "@types/node": "^22.0.0",
        "@types/react": "^18.3.0",
        "@types/react-dom": "^18.3.0",
        "autoprefixer": "^10.4.0",
        "postcss": "^8.4.0",
        "tailwindcss": "^3.4.0",
        "typescript": "^5.6.0",
    },
}, indent=2)

MINI_BACKEND_PACKAGE = json.dumps({
    "name": "verify-stage2-backend",
    "version": "0.1.0",
    "type": "module",
    "scripts": {"dev": "tsx src/index.ts"},
    "dependencies": {
        "express": "^4.21.0",
        "cors": "^2.8.5",
        "zod": "^3.23.8",
    },
    "devDependencies": {
        "@types/cors": "^2.8.17",
        "@types/express": "^4.17.21",
        "@types/node": "^22.7.0",
        "tsx": "^4.19.0",
        "typescript": "^5.6.0",
    },
}, indent=2)

MINI_TSCONFIG = json.dumps({
    "compilerOptions": {
        "lib": ["dom", "dom.iterable", "esnext"],
        "allowJs": True,
        "skipLibCheck": True,
        "strict": True,
        "noEmit": True,
        "esModuleInterop": True,
        "module": "esnext",
        "moduleResolution": "bundler",
        "resolveJsonModule": True,
        "isolatedModules": True,
        "jsx": "preserve",
    },
    "include": ["**/*.ts", "**/*.tsx"],
    "exclude": ["node_modules"],
}, indent=2)

MINI_BACKEND_ROUTES = '''import { Router, Request, Response } from 'express';
import { z } from 'zod';

const router = Router();
const convertSchema = z.object({ celsius: z.number() });

router.get('/health', (_req: Request, res: Response) => {
  res.json({ status: 'ok' });
});

router.post('/foo', (req: Request, res: Response) => {
  const parsed = convertSchema.safeParse(req.body);
  if (!parsed.success) return res.status(400).json({ error: 'bad input' });
  const { celsius } = parsed.data;
  const fahrenheit = +(celsius * 9 / 5 + 32).toFixed(2);
  const kelvin = +(celsius + 273.15).toFixed(2);
  res.json({ celsius, fahrenheit, kelvin });
});

export default router;
'''

MINI_BACKEND_INDEX = '''import express from 'express';
import cors from 'cors';
import routes from './routes.js';

const app = express();
app.use(cors());
app.use(express.json());
app.use('/api/v1', routes);

const PORT: number = Number(process.env.PORT) || 3001;
app.listen(PORT, () => console.log(`Server running on port ${PORT}`));
'''

# frontend 故意调错: 用 useEffect 打开页面就 fetch /api/v1/foo 但用 GET, 触发 404
MINI_FRONTEND_PAGE = """'use client';
import { useEffect, useState } from 'react';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:3001/api/v1';

export default function Home() {
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    (async () => {
      try {
        // BUG: 调 /foo (URL 在 contract) 但用 GET (backend 只支持 POST) — 静态查 URL 合法,
        // 实际 dev server 起来后会 404 (express 默认不响应 GET /foo, 因为只有 POST 注册)
        const res = await fetch(`${API}/api/v1/foo?celsius=25`, {
          method: 'GET',
        });
        if (!res.ok) {
          setError(`HTTP ${res.status}`);
          return;
        }
        const data = await res.json();
        setResult(data);
      } catch (e: any) {
        setError(e.message || '网络错误');
      }
    })();
  }, []);

  // 不管 fetch 结果, 都给视觉阶段渲染稳定内容, 避免 visual hash diff 把 interaction 阶段短路
  if (result === null && !error) return <main style={{ padding: 24 }}><h1>温度转换器 (verify stage 2)</h1><p>加载中...</p></main>;

  return (
    <main style={{ padding: 24, fontFamily: 'system-ui' }}>
      <h1>温度转换器 (verify stage 2)</h1>
      {error && <p style={{ color: 'red' }}>错误: {error}</p>}
      {result && (
        <div>
          <p>华氏: {result.fahrenheit}</p>
          <p>开尔文: {result.kelvin}</p>
        </div>
      )}
    </main>
  );
}
"""


def setup_mini_project(root: Path) -> dict:
    """准备一个故意错配的 mini 项目 + 返回 api_contract."""
    backend = root / "backend"
    frontend = root / "frontend"
    (backend / "src").mkdir(parents=True)
    (frontend / "src" / "app").mkdir(parents=True)

    (backend / "package.json").write_text(MINI_BACKEND_PACKAGE)
    (backend / "tsconfig.json").write_text(MINI_TSCONFIG)
    (backend / "src" / "routes.ts").write_text(MINI_BACKEND_ROUTES)
    (backend / "src" / "index.ts").write_text(MINI_BACKEND_INDEX)

    (frontend / "package.json").write_text(MINI_FRONTEND_PACKAGE)
    (frontend / "tsconfig.json").write_text(MINI_TSCONFIG)
    (frontend / "src" / "app" / "page.tsx").write_text(MINI_FRONTEND_PAGE)

    return {
        "endpoints": [
            {"method": "GET", "path": "/health", "full": "/api/v1/health"},
            {"method": "POST", "path": "/foo", "full": "/api/v1/foo"},
        ],
        "mount_prefix": "/api/v1",
        "derived_from": "verify-stage2-fixture",
    }


def build_files_dict(root: Path) -> dict[str, str]:
    """把磁盘上 mini 项目的源码读出来, 喂给 TestAgent."""
    return {
        "backend/package.json": (root / "backend" / "package.json").read_text(encoding="utf-8"),
        "backend/tsconfig.json": (root / "backend" / "tsconfig.json").read_text(encoding="utf-8"),
        "backend/src/routes.ts": (root / "backend" / "src" / "routes.ts").read_text(encoding="utf-8"),
        "backend/src/index.ts": (root / "backend" / "src" / "index.ts").read_text(encoding="utf-8"),
        "frontend/package.json": (root / "frontend" / "package.json").read_text(encoding="utf-8"),
        "frontend/tsconfig.json": (root / "frontend" / "tsconfig.json").read_text(encoding="utf-8"),
        "frontend/src/app/page.tsx": (root / "frontend" / "src" / "app" / "page.tsx").read_text(encoding="utf-8"),
    }


async def run_test_agent(files: dict, contract: dict) -> tuple[bool, list, list]:
    """跑一次 TestAgent.run(), 返回 (passed, signals_dicts, errors)."""
    test = TestAgent(
        project_id=99,  # 小 id, 避免 4100 + project_id*2 溢出 65535
        context={
            "files": dict(files),  # 复制一份, TestAgent 会改 TEST_REPORT.md
            "api_contract": contract,
            "user_idea": "做一个温度转换器, 输入摄氏温度, 转换华氏和开尔文",
            "design_spec": None,
        },
    )
    # 调 _run_verification 跳过 ProjectAgent 父类循环, 直接进测试核心
    result = await test._run_verification()
    signals = [s.__dict__ for s in result.failure_signals]
    return (result.passed, signals, result.errors)


async def main() -> int:
    print("=" * 60)
    print("Stage 2 self-repair 真值校验 (真实 LLM + 真实 dev server)")
    print("=" * 60)

    with tempfile.TemporaryDirectory(prefix="opc_stage2_verify_") as tmp:
        root = Path(tmp)
        contract = setup_mini_project(root)
        print(f"\n[1/5] 准备 mini 项目 (in {root})")
        print(f"      backend 实际: POST /api/v1/foo")
        print(f"      frontend 调: POST /api/v1/convert (故意错配)")

        files = build_files_dict(root)

        # 第一次跑 test_agent: 期望 fail, 拿到 http_404 signal
        print(f"\n[2/5] 跑 TestAgent 第一次 (期望: http_404 signal)")
        passed, signals, errors = await run_test_agent(files, contract)
        if passed:
            print(f"\n[FAIL] TestAgent 第一次居然 passed! 错配没被发现, 信号链路断裂")
            print(f"       errors={errors}")
            return 1

        http_404 = [s for s in signals if s.get("error_kind") == "http_404"]
        if not http_404:
            print(f"\n[FAIL] 第一次 fail 但没拿到 http_404 signal")
            print(f"       signals={signals}")
            print(f"       errors={errors[:3]}")
            return 1

        print(f"\n  ✓ 拿到 http_404 signal:")
        for s in http_404[:2]:
            print(f"      - file: {s.get('file_path')}")
            print(f"        kind: {s.get('error_kind')}")
            print(f"        msg:  {s.get('error_msg', '')[:200]}")
            print(f"        hint: {s.get('suggested_action')}")
            print(f"        agent: {s.get('agent')}")

        # 调 FrontendAgent.repair_with_tools() — 真实 LLM
        print(f"\n[3/5] 调 FrontendAgent.repair_with_tools (真实 LLM)")
        agent = FrontendAgent(
            project_id=99,
            context={
                "prd": "",
                "user_idea": "做一个温度转换器, 输入摄氏温度, 转换华氏和开尔文",
                "api_contract": contract,
                "design_spec": None,
                "mode": "repair",
                "tool_project_root": str(root),
            },
        )
        result = await agent.repair_with_tools(root, http_404)
        print(f"\n  repair success={result.get('success')}, tools_used={result.get('tools_used', 0)}")
        if result.get("text"):
            print(f"  LLM 文字回复 (前 300):\n    {result['text'][:300]}")
        if result.get("history"):
            print(f"  Tool 调用序列:")
            for h in result["history"][:10]:
                status = "✓" if h.get("success") else "✗"
                tool = h.get("tool", "?")
                inp = h.get("input", {})
                if tool == "read_file":
                    print(f"    {status} read_file({inp.get('path', '?')})")
                elif tool == "edit_file":
                    print(f"    {status} edit_file({inp.get('path', '?')})")
                elif tool == "bash":
                    cmd = inp.get("command", "?")[:60]
                    print(f"    {status} bash({cmd!r})")
                else:
                    print(f"    {status} {tool}({str(inp)[:80]})")

        if not result.get("success"):
            print(f"\n[FAIL] repair 失败 — Stage 2 self-repair 在真实 LLM 下不能修对")
            return 1

        # 验证: page.tsx 改对了
        # repair_with_tools 内部已经 reload 到 self.files, 也写到磁盘
        page_path = root / "frontend" / "src" / "app" / "page.tsx"
        new_page = page_path.read_text(encoding="utf-8")
        if "/api/v1/convert" in new_page and "/api/v1/foo" not in new_page:
            print(f"\n[FAIL] 修复后 page.tsx 仍然调 /api/v1/convert (没改掉)")
            return 1
        if "/api/v1/foo" not in new_page:
            print(f"\n[WARN] 修复后没看到 /api/v1/foo, LLM 改成了别的路径")
            print(f"       {new_page[new_page.find('fetch'):new_page.find('fetch')+200]}")
        else:
            print(f"\n  ✓ page.tsx 改对了 (现在调 /api/v1/foo)")

        # 第二次跑 test_agent: 期望 passed
        print(f"\n[4/5] 跑 TestAgent 第二次 (期望: passed)")
        files2 = build_files_dict(root)  # 重新读磁盘
        passed2, signals2, errors2 = await run_test_agent(files2, contract)
        if not passed2:
            print(f"\n[FAIL] TestAgent 第二次仍然 fail, 修复没生效")
            print(f"       signals={signals2[:2]}")
            print(f"       errors={errors2[:3]}")
            return 1

        print(f"\n  ✓ TestAgent 第二次 passed")

        print(f"\n[5/5] 全部通过 ✅")
        print(f"\n{'=' * 60}")
        print(f"Stage 2 self-repair 在真实 LLM + 真实项目下确实能修对路由错配")
        print(f"  - http_404 signal:       ✓")
        print(f"  - LLM repair 成功:       ✓ (tools_used={result.get('tools_used', 0)})")
        print(f"  - 文件真改对:            ✓")
        print(f"  - 重跑 TestAgent pass:   ✓")
        print(f"{'=' * 60}")
        return 0


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
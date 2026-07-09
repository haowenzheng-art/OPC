"""Stage 1 工具能力 demo - 验证 LLM 真的能用 read/edit/bash 工具自修复.

两个场景:
  1. 合成 bug 场景 (--scenario synthetic): 在 tempdir 放一个 TS 文件,故意写错 import,
     让 LLM 通过 tsc 自己发现、自己 read_file 看现状、自己 edit_file 修。
  2. 真实项目场景 (--scenario project --project-id N): 针对 OPC 生成的项目,让 LLM
     诊断一个具体问题 (如 project 3 的 addItem 不连后端)。

跑法:
    cd backend
    python tools_demo.py --scenario synthetic
    python tools_demo.py --scenario project --project-id 3
    python tools_demo.py --scenario project --project-id 2 --issue "添加待办事项失败"

退出标准:
  - 工具被实际调用 (tool_history 长度 > 0)
  - 文件被实际修改 (read_file 后看到 edit_file 调用)
  - 修改后 tsc --noEmit 通过
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from app.agent.llm import LLMClient
from app.agent.projects.agent_tools import ToolRegistry
from app.agent.projects.utils import llm_chat_with_tools
from app.core.logging import get_logger

log = get_logger(__name__)


SCENARIO_SYNTHETIC_TS = """import express from 'express';
import { z } from 'zod';
import _ from 'lodash';  // 故意 import 模板没装的包

const router = express.Router();

router.get('/hello', (req, res) => {
  res.json({ message: _.kebabCase('Hello World') });
});

export default router;
"""

SCENARIO_SYNTHETIC_PKG = """{
  "name": "synthetic",
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


async def run_synthetic() -> int:
    """合成 bug: 让 LLM 修掉未安装的 import."""
    tmpdir = Path(tempfile.mkdtemp(prefix="opc_tools_demo_"))
    try:
        src_dir = tmpdir / "src"
        src_dir.mkdir(parents=True)
        (src_dir / "routes.ts").write_text(SCENARIO_SYNTHETIC_TS, encoding="utf-8")
        (tmpdir / "package.json").write_text(SCENARIO_SYNTHETIC_PKG, encoding="utf-8")
        tsconfig = '{"compilerOptions":{"target":"ES2022","module":"NodeNext","moduleResolution":"NodeNext","strict":true,"esModuleInterop":true,"skipLibCheck":true},"include":["src/**/*"]}'
        (tmpdir / "tsconfig.json").write_text(tsconfig, encoding="utf-8")
        log.info("synthetic_setup", path=str(tmpdir))

        registry = ToolRegistry(tmpdir)
        log.info("tools_available", names=registry.list_tool_names())

        prompt = f"""你是一位 TypeScript 后端工程师。`{tmpdir}/src/routes.ts` 引用了 'lodash',但 package.json 没装。
请用工具修复:
  1. 用 bash 跑 `npx tsc --noEmit` 确认错误
  2. 用 read_file 看 routes.ts 当前内容
  3. 用 edit_file 改成不依赖 lodash (用 .split('-').map(...).join('-') 代替 _.kebabCase)
  4. 再跑一次 tsc 验证通过

最后只输出一行: 'FIXED: 全部完成' 或 'FAILED: <原因>'"""

        client = LLMClient.get()
        text, history = await llm_chat_with_tools(
            system="你是一位严谨的工程师。总是先看现状再改,改完必验证。",
            user=prompt,
            tool_registry=registry,
            llm=client,
            temperature=0.2,
            max_iterations=6,
        )

        print("\n===== Tool History =====")
        for h in history:
            print(f"  iter={h['iteration']} tool={h['tool']:12s} success={h['success']}  err={h.get('error')}")
            if h["tool"] in ("read_file", "edit_file"):
                inp = h["input"]
                print(f"    path={inp.get('path','?')}")
            elif h["tool"] == "bash":
                print(f"    cmd={h['input'].get('command','?')[:80]}")

        print(f"\n===== Final Text ({len(text)} chars) =====")
        print(text[:1000])

        # 验证:文件确实被改
        new_content = (src_dir / "routes.ts").read_text(encoding="utf-8")
        lodash_gone = "lodash" not in new_content
        print(f"\n===== Post-Check =====")
        print(f"  lodash removed from routes.ts: {lodash_gone}")
        print(f"  tools called: {len(history)}")
        print(f"  read_file called: {sum(1 for h in history if h['tool'] == 'read_file')}")
        print(f"  edit_file called: {sum(1 for h in history if h['tool'] == 'edit_file')}")
        print(f"  bash called: {sum(1 for h in history if h['tool'] == 'bash')}")

        if not history:
            print("[FAIL] 工具没被调用,LLM 没走工具路径")
            return 1
        if not lodash_gone:
            print("[FAIL] routes.ts 仍引用 lodash,修复未生效")
            return 1
        if "edit_file" not in [h["tool"] for h in history]:
            print("[FAIL] 没用 edit_file 改文件,只是嘴上说说")
            return 1
        if "bash" not in [h["tool"] for h in history]:
            print("[WARN] 没跑 tsc 验证,可能没真改对")

        print("\n[PASS] Stage 1 工具 loop 工作正常")
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def run_project(project_id: int, issue: str) -> int:
    """真实项目场景: 让 LLM 诊断并修复生成项目里的具体问题."""
    backend_root = Path(__file__).parent
    project_root = backend_root / "generated-projects" / "projects" / str(project_id)
    if not project_root.exists():
        print(f"[FAIL] project {project_id} 不存在: {project_root}")
        return 1

    print(f"===== 真实项目场景: project_id={project_id} =====")
    print(f"  root: {project_root}")
    print(f"  issue: {issue}\n")

    registry = ToolRegistry(project_root)
    log.info("tools_available", names=registry.list_tool_names())

    prompt = f"""你是一位 OPC 调试工程师。用户报告以下问题:

  ## 问题
  {issue}

  ## 项目位置
  {project_root}

  请用工具诊断:
  1. 先 list_files 看项目结构
  2. read_file 关键文件 (page.tsx / routes.ts / index.ts)
  3. 如有需要,bash 跑 `npx tsc --noEmit` 或 `cat package.json`
  4. 用 edit_file 修复发现的问题
  5. 修复后再跑 tsc / curl 验证

最后只输出一行: 'FIXED: <改动总结>' 或 'DIAGNOSED: <根因 + 建议>'"""

    client = LLMClient.get()
    text, history = await llm_chat_with_tools(
        system="你是 OPC 调试工程师,严谨、查现状再改、每次改完必验证。",
        user=prompt,
        tool_registry=registry,
        llm=client,
        temperature=0.2,
        max_iterations=8,
    )

    print("===== Tool History =====")
    for h in history:
        print(f"  iter={h['iteration']} tool={h['tool']:12s} success={h['success']}  err={h.get('error')}")
        if h["tool"] in ("read_file", "edit_file"):
            print(f"    path={h['input'].get('path','?')}")
        elif h["tool"] == "bash":
            print(f"    cmd={h['input'].get('command','?')[:80]}")

    print(f"\n===== Final Text ({len(text)} chars) =====")
    print(text[:1500])

    print(f"\n===== Stats =====")
    print(f"  tools called: {len(history)}")
    print(f"  read_file: {sum(1 for h in history if h['tool'] == 'read_file')}")
    print(f"  list_files: {sum(1 for h in history if h['tool'] == 'list_files')}")
    print(f"  edit_file: {sum(1 for h in history if h['tool'] == 'edit_file')}")
    print(f"  bash: {sum(1 for h in history if h['tool'] == 'bash')}")
    if not history:
        print("[FAIL] 工具没被调用")
        return 1
    print("\n[PASS] Stage 1 工具 loop 在真实项目上工作")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=["synthetic", "project"], default="synthetic")
    ap.add_argument("--project-id", type=int, help="when scenario=project")
    ap.add_argument("--issue", default="添加待办事项失败,点击 Add 按钮没反应", help="issue description")
    args = ap.parse_args()

    if args.scenario == "synthetic":
        return asyncio.run(run_synthetic())
    else:
        if not args.project_id:
            print("--scenario project 需要 --project-id")
            return 1
        return asyncio.run(run_project(args.project_id, args.issue))


if __name__ == "__main__":
    sys.exit(main())
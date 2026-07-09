"""Frontend Agent - 根据 PRD 和后端契约生成前端代码.

闭环改造:
- 消费机器可读 api_contract (JSON),不再是 markdown api_spec
- 生成 page.tsx 后用 reconcile_fetch_urls 机械修正任何不在契约里的 fetch URL
- 扫 page.tsx + layout.tsx 的 import (修之前只扫 page.tsx 的 bug)
- verify.py 静态验证 + RetryState 循环检测
"""
from __future__ import annotations

import json
from typing import Any

from app.agent.projects.api_contract import reconcile_fetch_urls
from app.agent.projects.base_agent import AgentAction, AgentState, ProjectAgent
from app.agent.projects.imports import scan_third_party_imports
from app.agent.projects.retry import RetryState
from app.agent.projects.utils import extract_code_block, llm_chat
from app.agent.projects.verify import verify_frontend
from app.core.logging import get_logger

log = get_logger(__name__)


# Next.js / React 生态内置 (来自模板 package.json)
_FRONTEND_TEMPLATE_DEPS = {
    "next", "react", "react-dom",
    "@types/node", "@types/react", "@types/react-dom",
    "autoprefixer", "postcss", "tailwindcss", "typescript",
}


class FrontendAgent(ProjectAgent):
    role = "frontend"

    def __init__(self, project_id: int, context: dict[str, Any]):
        super().__init__(project_id, context)
        self.prd = context.get("prd", "")
        self.user_idea = context.get("user_idea", "")
        # 优先用机器可读契约,fallback 到 markdown api_spec (向后兼容)
        self.api_contract = context.get("api_contract")
        self.api_spec = context.get("api_spec", "")
        self.design_spec = context.get("design_spec")
        self.feedback = context.get("feedback", "")
        self.files: dict[str, str] = {}
        self.retry_state = RetryState.from_dict(context.get("retry_state"))
        self.verify_errors: list[str] = []
        self._verified = False

    async def perceive(self) -> AgentState:
        return AgentState(
            project_id=self.project_id,
            role=self.role,
            data={"prd": self.prd, "files": list(self.files.keys())},
        )

    async def reason(self, state: AgentState) -> AgentAction:
        if not self.files or not self._verified:
            return AgentAction(type="GENERATE_FRONTEND", payload=state.data)
        return AgentAction(type="WAIT")

    async def act(self, action: AgentAction) -> None:
        if action.type == "GENERATE_FRONTEND":
            log.info("frontend_generating", project_id=self.project_id, attempts=self.retry_state.attempts + 1)
            while True:
                self.retry_state.record_attempt()
                await self._generate_files()
                result = await verify_frontend(self.files, self.project_id, self.api_contract)
                if result.passed:
                    self._verified = True
                    break
                err_blob = "\n".join(result.errors)
                self.verify_errors = result.errors
                log.warning("frontend_verify_failed", project_id=self.project_id,
                            attempt=self.retry_state.attempts, errors=result.errors[:3])
                if not self.retry_state.should_retry(err_blob):
                    log.warning("frontend_retry_stuck_or_capped", project_id=self.project_id,
                                stuck=self.retry_state.stuck, attempts=self.retry_state.attempts)
                    break
                self.feedback = f"上次生成的代码验证失败,请修复以下问题:\n{err_blob}\n\n请重新生成完整的 page.tsx,确保修复上述问题。"
            self.record_action("GENERATE_FRONTEND")
            await self.save_memory(
                observation=f"生成前端文件: {list(self.files.keys())} (attempts={self.retry_state.attempts})",
                insight="Next.js + Tailwind CSS 是默认技术栈",
                importance=7,
            )
            self.mark_done()

    async def _generate_files(self) -> None:
        package_json = """{
  "name": "generated-frontend",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start"
  },
  "dependencies": {
    "next": "14.2.0",
    "react": "^18.3.0",
    "react-dom": "^18.3.0"
  },
  "devDependencies": {
    "@types/node": "^22.0.0",
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "autoprefixer": "^10.4.0",
    "postcss": "^8.4.0",
    "tailwindcss": "^3.4.0",
    "typescript": "^5.6.0"
  }
}
"""
        tsconfig_json = """{
  "compilerOptions": {
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": true,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "paths": {
      "@/*": ["./*"]
    }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx"],
  "exclude": ["node_modules"]
}
"""
        next_config = """/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
};

export default nextConfig;
"""
        postcss_config = """module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
"""
        tailwind_config = """/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './src/**/*.{js,ts,jsx,tsx,mdx}',
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {},
  },
  plugins: [],
};
"""
        global_css = """@tailwind base;
@tailwind components;
@tailwind utilities;
"""
        app_layout = await self._generate_layout_tsx()
        app_page = await self._generate_page_tsx()

        # 机械修正:把 page.tsx 中不在契约里的 fetch URL 改写到契约端点
        # 这一步不靠 LLM 纪律,机械保证 frontend fetch URL 与 backend routes 对齐
        if self.api_contract:
            app_page, rewritten = reconcile_fetch_urls(app_page, self.api_contract)
            if rewritten:
                log.info("frontend_fetch_urls_reconciled", project_id=self.project_id, rewritten=rewritten)

        # 扫 page.tsx + layout.tsx 的 import (之前 bug: 只扫 page.tsx)
        all_code = "\n".join([app_page, app_layout])
        extra_deps = scan_third_party_imports(all_code) - _FRONTEND_TEMPLATE_DEPS
        if extra_deps:
            pkg = json.loads(package_json)
            deps = pkg.setdefault("dependencies", {})
            for dep in extra_deps:
                if dep not in deps:
                    deps[dep] = "*"
                    log.info("frontend_agent_inject_dep", dep=dep)
            package_json = json.dumps(pkg, indent=2) + "\n"
            log.info("frontend_agent_extra_deps", deps=sorted(extra_deps))

        self.files["frontend/package.json"] = package_json
        self.files["frontend/tsconfig.json"] = tsconfig_json
        self.files["frontend/next.config.mjs"] = next_config
        self.files["frontend/postcss.config.js"] = postcss_config
        self.files["frontend/tailwind.config.js"] = tailwind_config
        self.files["frontend/src/app/globals.css"] = global_css
        self.files["frontend/src/app/layout.tsx"] = app_layout
        self.files["frontend/src/app/page.tsx"] = app_page
        self.files["frontend/README.md"] = "# Generated Frontend\n\nRun `npm install && npm run dev`"

    async def _generate_layout_tsx(self) -> str:
        return """export const metadata = {
  title: 'Generated App',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
"""

    async def _generate_page_tsx(self) -> str:
        # 优先用机器可读契约,fallback 到 markdown
        if self.api_contract:
            # json.dumps 在 f-string 里可能产生 {...} 被 Python f-string 误解析,
            # 用 str.replace 把 { → {{ 和 } → }} 转义 (不破坏 JSON 语义)。
            safe_json = json.dumps(self.api_contract, indent=2, ensure_ascii=False).replace("{", "{{").replace("}", "}}")
            api_block = "后端 API 契约 (JSON, fetch URL 必须严格匹配 endpoints[*].full):\n" + safe_json
        else:
            api_block = "后端 API 设计:\n" + self.api_spec

        feedback_block = "\n\n## 上次生成的问题 (请修复)\n" + self.feedback + "\n" if self.feedback else ""

        # 用户原始需求（如果有具体要求，需要尊重）
        user_idea_block = ""
        if self.user_idea:
            user_idea_block = "\n\n## 用户原始需求 (请尽量满足其中的具体要求)\n" + self.user_idea + "\n"

        # Design Agent 输出的视觉规范 (主色调/字体/间距/组件 class)
        # 优先用 design_spec, fallback 到内置规范
        if self.design_spec:
            design_safe = json.dumps(self.design_spec, indent=2, ensure_ascii=False).replace("{", "{{").replace("}", "}}")
            design_block = "## 视觉设计规范 (Design Agent 已生成, 必须严格遵循)\n" + design_safe + "\n\n"
            design_requirements = """- 配色: 必须使用 design_spec.palette 里的颜色 (如 bg-{primary}, hover:bg-{primary_hover})
  替换 {primary} 等占位符为实际颜色值 (如 "bg-blue-500")
- 字体: 使用 design_spec.typography 里的 class (h1/h2/body/small)
- 间距: 使用 design_spec.spacing 里的 class (container/section_gap/list_gap)
- 组件: 使用 design_spec.components 里的模板, 替换占位符
- 重要: 所有 class 必须直接写在 JSX 中, 不要绕一圈变量"""
        else:
            design_block = ""
            design_requirements = """- 配色: 使用 Tailwind 内置颜色(如 blue-500, gray-100, emerald-500 等)，不要用 arbitrary values 如 bg-[#xxx]
  - 圆角: 表单输入框 rounded-lg，按钮 rounded-lg 或 rounded-full，卡片 rounded-xl
  - 间距: 使用 Tailwind 间距系统 (gap-2, gap-4, p-4, mb-4, space-y-3 等)
  - 阴影: 卡片使用 shadow-sm 或 shadow-md，不要用 shadow-lg 除非需要突出
  - 字体: text-sm 用于辅助文字，text-lg 用于标题，text-2xl 用于页面大标题
  - 列表项: 每项之间用 gap-2 或 space-y-2 分隔，不要贴在一起
  - 表单: 输入框和按钮在同一行时用 flex 和 gap-2，不要堆叠
  - 响应式: 容器 max-w-2xl mx-auto 居中，移动端自动适配"""

        # JS 模板字符串含 `${...}`，不能在 Python f-string 里直接写 `${`。
        # 用普通字符串 + 拼接解决。
        correct_example = 'fetch(`${API}/api/v1/todos`)'
        wrong_example1 = "const EP = '/api/v1/todos'; fetch(`${API}${{EP}}`)"
        wrong_example2 = "const TIME_ENDPOINT = '/api/v1/time/beijing'; fetch(`${API}${{TIME_ENDPOINT}}`)"

        prompt_parts = [
            "根据以下 PRD、后端 API 契约和用户需求，生成一个 Next.js 14 App Router 页面 (page.tsx)。\n",
            "\nPRD:\n",
            self.prd,
            user_idea_block,
            "\n",
            api_block,
            feedback_block,
            design_block,
            """要求:
- 使用 React Server Component + Client Component 混合
""",
            design_requirements,
            """
- 页面需要包含: 列表展示、创建表单、删除/完成操作
- API base URL 从环境变量读取, 默认 http://localhost:3001/api/v1
- fetch URL 必须用契约里 endpoints[*].full 的路径,不要自己猜路径
- **重要**: fetch() 调用里路径必须内联在模板字符串里,不要用单独的常量变量存放路径
  正确: """,
            correct_example,
            """
  错误: """,
            wrong_example1,
            """
  错误: """,
            wrong_example2,
            """
- 只输出 page.tsx 内容, 不包含 ``` 标记

## JSX 语法硬约束 (NEW-3, 2026-07-09)
- **每个开标签必须有匹配闭标签**: `<div>...</div>`, `<span>...</span>`, `<section>...</section>` 等
- **自闭合标签用 /> 结尾**: `<img ... />`, `<input ... />`, `<br />`, `<hr />`
- **嵌套顺序正确**: 内层先关, 外层后关 (例: `<div><span>x</span></div>` 不是 `<div><span>x</div></span>`)
- **条件渲染用三元或 &&**: `{cond ? <A/> : <B/>}` 或 `{cond && <A/>}`, 不要裸 `<A/>` 夹条件
- **表达式/JSX 中不要忘记 `return (...)` 包裹**: 多行 JSX 必带 `return ( <div>...</div> )`
- **不要遗漏 close**: 每个 map / 嵌套 div / fragment 必须数清括号; 写完通读一遍对数

示例结构:
'use client';
import { useEffect, useState } from 'react';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:3001/api/v1';

export default function Home() {
  const [items, setItems] = useState([]);
  ...
}
""",
        ]
        prompt = "".join(prompt_parts)
        code = await llm_chat(
            system="你是一位前端工程师。只输出 TypeScript/React 代码, 不解释。",
            user=prompt,
            temperature=0.3,
            llm=self.llm,
        )
        return extract_code_block(code, "tsx")

    def get_files(self) -> dict[str, str]:
        return self.files

    def get_retry_state(self) -> RetryState:
        return self.retry_state

    # ---- Stage 2: 工具修复 hook ----

    def _should_use_tools(self) -> bool:
        return self.context.get("mode") == "repair"

    def _tool_project_root(self) -> str | None:
        pr = self.context.get("tool_project_root")
        return str(pr) if pr else None

    async def repair_with_tools(
        self,
        tool_project_root: "Path",
        failure_signals: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Stage 2: 拿到 test_agent 的 FailureSignal 后,用工具局部 patch 前端.

        不调用 _generate_files() — 不重抽整个 page.tsx。
        LLM 通过 read_file 看现状、edit_file 改、bash 跑 tsc 验证。
        """
        from pathlib import Path
        from app.agent.projects.agent_tools import ToolRegistry
        from app.agent.projects.utils import llm_chat_with_tools

        if not self._should_use_tools():
            log.warning("frontend_repair_tools_disabled", project_id=self.project_id)
            return {"success": False, "history": [], "text": "tools disabled", "tools_used": 0}

        tool_project_root = Path(tool_project_root)
        if not tool_project_root.exists():
            log.error("frontend_repair_no_root", root=str(tool_project_root))
            return {"success": False, "history": [], "text": "tool_project_root 不存在", "tools_used": 0}

        signals_blob = "\n".join([
            f"- file: {s.get('file_path','?')}\n  kind: {s.get('error_kind','?')}\n  msg: {s.get('error_msg','?')[:300]}\n  hint: {s.get('suggested_action','?')}"
            for s in failure_signals
        ])

        prompt = f"""你是前端工程师,负责修复 OPC 前端的测试失败。

## 失败信号 (来自 TestAgent)
{signals_blob}

## 项目位置
{tool_project_root}

## 你的任务
不要重写整个 page.tsx! 用工具**局部 patch**,按以下顺序:

### Step 0 (强制): 读 api_contract.json
1. `read_file("backend/api_contract.json")` — 看 backend 实际暴露了哪些 endpoint (注意 endpoints[*].full 才是真路径)
2. 把**所有 endpoint full 路径**记下来,这是改 fetch URL 的唯一依据

### Step 1: 定位问题
3. `list_files("frontend/src/app")` 看结构
4. `read_file("frontend/src/app/page.tsx")` 看当前所有 fetch() 调用

### Step 2: 修
5. 对每个 fetch URL 与 api_contract 不一致的:
   - 用 `edit_file` 精确替换 fetch 路径字符串
   - old_text 必须**完整包含** fetch URL (含模板字符串部分 `${{API}}`),确保唯一匹配
   - new_text 用 api_contract 里**逐字**的 path (e.g. `/api/v1/convert`,不要自己拼)

### Step 3: 验证
6. `bash("npx tsc --noEmit --project frontend/tsconfig.json")` 确认改完没坏
7. (可选) `bash("curl -s http://localhost:3001/api/v1/convert -X POST -H 'Content-Type: application/json' -d '{{}}'")` 验证 URL 真的能命中 (如果不是空 contract)

## 重要约束
- **不要重写 page.tsx 整文件** — 只用 edit_file 改具体几行
- **不要改 design_spec.json 里规定的颜色/字体/间距** — 视觉规范不在本次修复范围
- **不要新增 import** — 如果需要的能力不在 page.tsx 已有的 import 里,先看是否已经存在
- **不要重复跑同一个 bash 命令** — 如果 tsc 已经报过同样的错, 改文件, 不要再跑 tsc; 改完再跑一次确认
- **不要连续读同一个文件** — 读完一次后记住内容, 真要再读才再调 read_file
- 如果 `read_file` 拿到的 fetch URL 已经正确,但 test 还是失败,**那问题可能在 backend,不是你的事** — 直接报 FAILED,不要瞎改

修完只输出一行: 'REPAIRED: <改动总结>' 或 'FAILED: <未能修复的原因>'"""

        registry = ToolRegistry(tool_project_root)
        log.info("frontend_repair_start", project_id=self.project_id, signals=len(failure_signals), root=str(tool_project_root))

        text, history = await llm_chat_with_tools(
            system="你是严谨的前端工程师。看现状再改,fetch URL 必须严格匹配 api_contract,改完必验证。",
            user=prompt,
            tool_registry=registry,
            llm=self.llm,
            temperature=0.2,
            max_tokens=4096,
            max_iterations=8,
        )

        for h in history:
            self.actions.append(f"repair_tool:{h['tool']}:{'ok' if h['success'] else 'fail'}")

        self._reload_frontend_files_from_disk(tool_project_root)

        # success 判定: 三种情况都算修好
        # 1. LLM 文字明确说 "REPAIRED" (最明确)
        # 2. history 里有成功的 edit_file (改了文件, 即便 LLM 没明说)
        # 3. 排除: LLM 文字明确说 "FAILED" 且没 edit_file 成功
        said_repaired = "REPAIRED" in text.upper() and "FAILED" not in text.upper().split("REPAIRED")[0]
        said_failed = "FAILED" in text.upper() and "REPAIRED" not in text.upper()
        successful_edits = [h for h in history if h.get("tool") == "edit_file" and h.get("success")]
        success = said_repaired or (bool(successful_edits) and not said_failed)

        log.info(
            "frontend_repair_done",
            project_id=self.project_id,
            tools_used=len(history),
            success=success,
            said_repaired=said_repaired,
            said_failed=said_failed,
            successful_edits=len(successful_edits),
        )
        return {
            "success": success,
            "history": history,
            "text": text,
            "tools_used": len(history),
        }

    def _reload_frontend_files_from_disk(self, project_root: "Path") -> None:
        """把磁盘 frontend/ 下的最新文件回灌到 self.files."""
        from pathlib import Path
        root = Path(project_root) / "frontend"
        if not root.exists():
            return
        for src in root.rglob("*"):
            if src.is_file() and src.suffix in (".ts", ".tsx", ".json", ".css", ".mjs", ".js"):
                # 跳过 .next / node_modules (虽然不太可能在这里)
                if any(part in src.parts for part in ("node_modules", ".next")):
                    continue
                rel = src.relative_to(root).as_posix()
                key = f"frontend/{rel}"
                try:
                    self.files[key] = src.read_text(encoding="utf-8", errors="replace")
                except OSError as e:
                    log.warning("frontend_reload_skip", path=key, error=str(e))

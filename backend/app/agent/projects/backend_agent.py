"""Backend Agent - 根据 PRD 生成后端代码.

闭环改造:
- 生成后用 verify.py 静态验证 (tsc + import resolution + contract 可派生)
- 失败则带 feedback 重生成,RetryState 做循环检测 (同错误两次=stuck)
- 生成 routes.ts 后派生 api_contract.json (机器可读),替换 markdown api_spec
"""
from __future__ import annotations

import json
from typing import Any

from app.agent.projects.api_contract import derive_api_contract, derive_mount_prefix
from app.agent.projects.base_agent import AgentAction, AgentState, ProjectAgent
from app.agent.projects.imports import merge_deps_into_package_json, scan_third_party_imports
from app.agent.projects.retry import RetryState
from app.agent.projects.utils import extract_code_block, llm_chat
from app.agent.projects.verify import verify_backend
from app.core.logging import get_logger

log = get_logger(__name__)


class BackendAgent(ProjectAgent):
    role = "backend"

    def __init__(self, project_id: int, context: dict[str, Any]):
        super().__init__(project_id, context)
        self.prd = context.get("prd", "")
        self.feedback = context.get("feedback", "")  # 重试时把上次错误喂回 prompt
        self.files: dict[str, str] = {}
        self.api_spec = ""
        self.api_contract: dict | None = None
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
            return AgentAction(type="GENERATE_BACKEND", payload=state.data)
        return AgentAction(type="WAIT")

    async def act(self, action: AgentAction) -> None:
        if action.type == "GENERATE_BACKEND":
            log.info("backend_generating", project_id=self.project_id, attempts=self.retry_state.attempts + 1)
            # 闭环: 生成 → 验证 → 失败带 feedback 重生成,直到通过或 stuck
            while True:
                self.retry_state.record_attempt()
                await self._generate_api_spec()
                await self._generate_files()
                result = await verify_backend(self.files, self.project_id)
                if result.passed:
                    self._verified = True
                    break
                # 验证失败,把错误喂回 prompt 重生成
                err_blob = "\n".join(result.errors)
                self.verify_errors = result.errors
                log.warning("backend_verify_failed", project_id=self.project_id,
                            attempt=self.retry_state.attempts, errors=result.errors[:3])
                if not self.retry_state.should_retry(err_blob):
                    log.warning("backend_retry_stuck_or_capped", project_id=self.project_id,
                                stuck=self.retry_state.stuck, attempts=self.retry_state.attempts)
                    break
                self.feedback = f"上次生成的代码验证失败,请修复以下问题:\n{err_blob}\n\n请重新生成完整的 routes.ts,确保修复上述问题。"
            self.record_action("GENERATE_BACKEND")
            await self.save_memory(
                observation=f"生成后端文件: {list(self.files.keys())} (attempts={self.retry_state.attempts})",
                insight="Express + TypeScript + Prisma 是默认技术栈",
                importance=7,
            )
            self.mark_done()

    async def _generate_api_spec(self) -> None:
        prompt = f"""根据以下 PRD, 输出后端 API 设计:

{self.prd}

请输出:
1. 主要数据实体 (Entity) 和字段
2. RESTful API 端点列表 (METHOD /path)
3. 使用 Express + TypeScript + Prisma + SQLite

用 Markdown 输出。"""
        self.api_spec = await llm_chat(
            system="你是一位后端架构师。请输出简洁的 API 设计文档。",
            user=prompt,
            temperature=0.2,
            llm=self.llm,
        )

    async def _generate_files(self) -> None:
        # 为了稳定和可控, 使用模板 + LLM 生成关键文件
        package_json = """{
  "name": "generated-backend",
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "tsx src/index.ts",
    "build": "tsc",
    "start": "node dist/index.js",
    "db:push": "prisma db push"
  },
  "dependencies": {
    "@prisma/client": "^5.20.0",
    "cors": "^2.8.5",
    "express": "^4.21.0",
    "zod": "^3.23.8"
  },
  "devDependencies": {
    "@types/cors": "^2.8.17",
    "@types/express": "^4.17.21",
    "@types/node": "^22.7.0",
    "prisma": "^5.20.0",
    "tsx": "^4.19.0",
    "typescript": "^5.6.0"
  }
}
"""
        tsconfig_json = """{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "outDir": "./dist",
    "rootDir": "./src",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true
  },
  "include": ["src/**/*"]
}
"""
        prisma_schema = await self._generate_prisma_schema()
        index_ts = await self._generate_index_ts()
        routes_ts = await self._generate_routes_ts()

        # LLM 可能 import 模板 package.json 里没声明的包 (如 date-fns)，导致
        # dev server 启动时报 ERR_MODULE_NOT_FOUND。扫描生成的 TS 代码，把
        # 模板 deps 之外的第三方包自动注入 package.json。
        all_code = "\n".join([index_ts, routes_ts])
        extra_deps = scan_third_party_imports(all_code) - {"express", "cors", "zod", "@prisma/client"}
        if extra_deps:
            package_json = merge_deps_into_package_json(package_json, extra_deps)
            log.info("backend_agent_extra_deps", deps=sorted(extra_deps))

        self.files["backend/package.json"] = package_json
        self.files["backend/tsconfig.json"] = tsconfig_json
        self.files["backend/prisma/schema.prisma"] = prisma_schema
        self.files["backend/src/index.ts"] = index_ts
        self.files["backend/src/routes.ts"] = routes_ts
        self.files["backend/README.md"] = "# Generated Backend\n\nRun `npm install && npm run dev`"

        # 派生机器可读契约 (从 routes.ts 静态分析),替换 markdown api_spec 给 frontend
        mount = derive_mount_prefix(index_ts)
        contract = derive_api_contract(routes_ts, mount)
        if contract is not None:
            self.api_contract = contract
            self.files["backend/api_contract.json"] = json.dumps(contract, indent=2, ensure_ascii=False)

    async def _generate_prisma_schema(self) -> str:
        prompt = f"""根据以下 PRD, 生成 Prisma schema。

{self.prd}

要求:
- 使用 SQLite provider
- 包含至少 2 个 model
- 每个 model 有 id, createdAt, updatedAt
- 只输出 schema 内容, 不包含 ``` 标记

示例格式:
generator client {{
  provider = "prisma-client-js"
}}

datasource db {{
  provider = "sqlite"
  url      = env("DATABASE_URL")
}}

model Todo {{
  id        String   @id @default(cuid())
  title     String
  completed Boolean  @default(false)
  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt
}}"""
        return await llm_chat(
            system="你是一位数据库专家。只输出 Prisma schema 代码, 不解释。",
            user=prompt,
            temperature=0.2,
            llm=self.llm,
        )

    async def _generate_index_ts(self) -> str:
        return """import express from 'express';
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

    async def _generate_routes_ts(self) -> str:
        feedback_block = f"\n\n## 上次生成的问题 (请修复)\n{self.feedback}\n" if self.feedback else ""
        prompt = f"""根据以下 PRD 和 API 设计, 生成 Express 路由代码 (TypeScript ESM)。

PRD:
{self.prd}

API Spec:
{self.api_spec}
{feedback_block}
要求:
- 使用 import/export (ESM)
- 使用 zod 做简单输入校验
- 使用内存数组做数据存储 (不连接真实 DB, 便于 demo)
- 每个端点必须用 router.METHOD('/path', ...) 形式注册,路径不要带 /api/v1 前缀 (mount 由 index.ts 处理)
- 只输出 routes.ts 内容, 不包含 ``` 标记

示例:
import {{ Router }} from 'express';
import {{ z }} from 'zod';

const router = Router();
const todos: any[] = [];

const createSchema = z.object({{ title: z.string() }});

router.get('/todos', (req, res) => res.json({{ data: todos }}));
router.post('/todos', (req, res) => {{
  const data = createSchema.parse(req.body);
  const todo = {{ id: String(todos.length + 1), title: data.title, completed: false }};
  todos.push(todo);
  res.json({{ data: todo }});
}});

export default router;"""
        code = await llm_chat(
            system="你是一位后端工程师。只输出 TypeScript 代码, 不解释。",
            user=prompt,
            temperature=0.2,
            llm=self.llm,
        )
        return extract_code_block(code, "typescript")

    def get_files(self) -> dict[str, str]:
        return self.files

    def get_api_spec(self) -> str:
        return self.api_spec

    def get_api_contract(self) -> dict | None:
        return self.api_contract

    def get_retry_state(self) -> RetryState:
        return self.retry_state

    # ---- Stage 2: 工具修复 hook ----

    def _should_use_tools(self) -> bool:
        return self.context.get("mode") == "repair"

    def _tool_project_root(self) -> str | None:
        # repair 模式:由 orchestrator 注入 project_root
        pr = self.context.get("tool_project_root")
        return str(pr) if pr else None

    async def repair_with_tools(
        self,
        tool_project_root: "Path",
        failure_signals: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Stage 2: 拿到 test_agent 的 FailureSignal 后,用工具局部 patch 不重抽.

        不调用 _generate_files() — 不重生成整个 backend。
        LLM 通过 read_file 看现状、edit_file 改、bash 跑 tsc 验证。
        """
        from pathlib import Path
        from app.agent.projects.agent_tools import ToolRegistry
        from app.agent.projects.utils import llm_chat_with_tools

        if not self._should_use_tools():
            log.warning("backend_repair_tools_disabled", project_id=self.project_id)
            return {"success": False, "history": [], "text": "tools disabled", "tools_used": 0}

        tool_project_root = Path(tool_project_root)
        if not tool_project_root.exists():
            log.error("backend_repair_no_root", root=str(tool_project_root))
            return {"success": False, "history": [], "text": "tool_project_root 不存在", "tools_used": 0}

        signals_blob = "\n".join([
            f"- file: {s.get('file_path','?')}\n  kind: {s.get('error_kind','?')}\n  msg: {s.get('error_msg','?')[:300]}\n  hint: {s.get('suggested_action','?')}"
            for s in failure_signals
        ])

        prompt = f"""你是后端工程师,负责修复 OPC 后端的测试失败。

## 失败信号 (来自 TestAgent)
{signals_blob}

## 项目位置
{tool_project_root}

## 你的任务
不要重写整个 routes.ts / index.ts! 用工具**局部 patch**,按以下顺序:

### Step 0 (强制): 读 api_contract.json
1. `read_file("backend/api_contract.json")` — 确认 backend 实际暴露了哪些 endpoint
2. 改 routes.ts 时,必须保证实际注册的路由与 api_contract 一致 (或者修改后跑一次 `_derive_contract` 同步)

### Step 1: 定位问题
3. `list_files("backend/src")` 看结构
4. `read_file("backend/src/routes.ts")` 看当前路由注册
5. 必要时 `bash("npx tsc --noEmit --project backend/tsconfig.json")` 重现错误

### Step 2: 修
6. 用 `edit_file` 精确改路由/zod schema/handler:
   - 缺 endpoint → 在合适位置加 `router.METHOD('/path', validate(schema), handler)`
   - handler 抛错 → 改 zod schema 或补 try/catch
   - mount prefix 错 → 改 index.ts 的 `app.use('/api/v1', routes)` (注意 mount 跟 routes 内路径的关系)

### Step 3: 验证
7. `bash("npx tsc --noEmit --project backend/tsconfig.json")` 确认编译过
8. (可选) `bash("npm run db:push 2>&1 | tail -20")` 确认 prisma schema OK (如果失败涉及 prisma)

## 重要约束
- **不要重写 routes.ts 整文件** — 只用 edit_file 改具体几行
- **不要改 prisma schema 字段名** — 改了 schema 就得重新 db push,本次修复不走那条路
- **不要重复跑同一个 bash 命令** — tsc 报过同样错就改文件, 改完再跑一次
- **不要连续读同一个文件** — 读完记住, 真要再读才再调
- 如果 contract 跟代码不一致,**改 routes.ts 让它对齐 contract** (contract 是 ground truth,Frontend 按 contract 调的)

修完只输出一行: 'REPAIRED: <改动总结>' 或 'FAILED: <未能修复的原因>'"""

        registry = ToolRegistry(tool_project_root)
        log.info("backend_repair_start", project_id=self.project_id, signals=len(failure_signals), root=str(tool_project_root))

        text, history = await llm_chat_with_tools(
            system="你是严谨的后端工程师。看现状再改,改完必验证,不靠 LLM 记忆。",
            user=prompt,
            tool_registry=registry,
            llm=self.llm,
            temperature=0.2,
            max_tokens=4096,
            max_iterations=8,
        )

        # 把 tool_history 同步到 self.actions 方便 orchestrator 看
        for h in history:
            self.actions.append(f"repair_tool:{h['tool']}:{'ok' if h['success'] else 'fail'}")

        # 同步 self.files (从磁盘 reload 涉及的 backend 文件)
        self._reload_backend_files_from_disk(tool_project_root)

        # success 判定: LLM 明确说 REPAIRED, 或 history 里有成功的 edit_file (没明确说 FAILED)
        said_repaired = "REPAIRED" in text.upper() and "FAILED" not in text.upper().split("REPAIRED")[0]
        said_failed = "FAILED" in text.upper() and "REPAIRED" not in text.upper()
        successful_edits = [h for h in history if h.get("tool") == "edit_file" and h.get("success")]
        success = said_repaired or (bool(successful_edits) and not said_failed)

        log.info(
            "backend_repair_done",
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

    def _reload_backend_files_from_disk(self, project_root: "Path") -> None:
        """把磁盘 backend/ 下的最新文件回灌到 self.files,保持 orchestrator 状态同步."""
        from pathlib import Path
        root = Path(project_root) / "backend"
        if not root.exists():
            return
        for src in root.rglob("*"):
            if src.is_file() and src.suffix in (".ts", ".tsx", ".json", ".prisma"):
                rel = src.relative_to(root).as_posix()
                key = f"backend/{rel}"
                try:
                    self.files[key] = src.read_text(encoding="utf-8", errors="replace")
                except OSError as e:
                    log.warning("backend_reload_skip", path=key, error=str(e))

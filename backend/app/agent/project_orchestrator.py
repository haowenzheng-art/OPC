"""OPC Project Orchestrator - 协调 6 个 Agent 完成项目生成,带闭环验证 + 两级 fallback.

状态机: idle -> planning -> developing -> testing -> deploying -> learning -> done
TESTS_FAIL → developing 回环 (带 feedback 重试), stuck 后触发 Level 1 (简化 PRD) / Level 2 (fallback 模板).
"""
from __future__ import annotations

from datetime import datetime
import asyncio
import json
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.projects.api_contract import reconcile_fetch_urls
from app.agent.projects.backend_agent import BackendAgent
from app.agent.projects.ceo_agent import CeoAgent
from app.agent.projects.contract_predictor import (
    get_default_crud_contract,
    predict_api_contract,
)
from app.agent.projects.credits_guard import CreditsExhausted, guard_llm_call
from app.agent.projects.design_agent import DesignAgent
from app.agent.projects.fallback import get_fallback_contract, load_fallback_files, simplify_prd
from app.agent.projects.frontend_agent import FrontendAgent
from app.agent.projects.ops_agent import OpsAgent
from app.agent.projects.pm_agent import PMAgent
from app.agent.projects.retry import RetryState
from app.agent.projects.test_agent import TestAgent
from app.core.logging import get_logger
from app.models import Artifact, Project
from app.services.storage import StorageBackend, get_storage_backend

log = get_logger(__name__)


class ProjectStateMachine:
    states = ["idle", "planning", "developing", "testing", "deploying", "learning", "done", "failed"]

    def __init__(self, user_idea: str):
        self.current_state = "idle"
        self.context: dict[str, Any] = {
            "user_idea": user_idea,
            "prd": None,
            "frontend_ready": False,
            "backend_ready": False,
            "tests_passed": False,
            "deploy_url": None,
            "errors": [],
            "retry_state": {},
            "fallback_used": False,
            "fallback_level": 0,
        }

    def send(self, event: str, payload: Any = None) -> None:
        old = self.current_state
        # ERROR event 优先级最高: 任何 state 都能转 failed (业务上: 任何阶段崩了都标 failed)
        if event == "ERROR":
            self.context["errors"].append(payload)
            self.current_state = "failed"
        elif self.current_state == "idle" and event == "START":
            self.current_state = "planning"
        elif self.current_state == "planning" and event == "PRD_DONE":
            self.context["prd"] = payload
            self.current_state = "developing"
        elif self.current_state == "developing":
            if event == "BACKEND_DONE":
                self.context["backend_ready"] = True
            elif event == "FRONTEND_DONE":
                self.context["frontend_ready"] = True
            if self.context["backend_ready"] and self.context["frontend_ready"]:
                self.current_state = "testing"
        elif self.current_state == "testing":
            if event == "TESTS_PASS":
                self.context["tests_passed"] = True
                self.current_state = "deploying"
            elif event == "TESTS_FAIL":
                self.current_state = "developing"
                self.context["backend_ready"] = False
                self.context["frontend_ready"] = False
        elif self.current_state == "deploying" and event == "DEPLOYED":
            self.context["deploy_url"] = payload
            self.current_state = "learning"
        elif self.current_state == "learning" and event == "LEARNING_DONE":
            self.current_state = "done"
        log.info("state_transition", old_state=old, new_state=self.current_state, transition_event=event)

    def get_state(self) -> str:
        return self.current_state


class ProjectOrchestrator:
    """OPC 项目编排器 — 带闭环验证 + 两级 fallback."""

    def __init__(
        self,
        project_id: int,
        user_idea: str,
        workflow_plan: str = "",
        storage: StorageBackend | None = None,
        progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        org_id: int | None = None,
    ):
        self.project_id = project_id
        self.user_idea = user_idea
        self.workflow_plan = workflow_plan
        self.storage = storage or get_storage_backend()
        self.progress_callback = progress_callback
        self.org_id = org_id
        self._credits_session = None  # async session 用于 credits 检查,由 caller 注入
        self.state_machine = ProjectStateMachine(user_idea)
        self.files: dict[str, str] = {}
        self.credits_used = 0
        self.api_contract: dict | None = None
        self.design_spec: dict | None = None
        # P1-4: LLM 成本跟踪
        from app.agent.cost_tracker import CostTracker
        self.cost_tracker = CostTracker(project_id=project_id)

    def set_credits_session(self, session: Any) -> None:
        """由 caller 注入 DB session,用于 credits 检查. 不能在 __init__ 里持有 session — 太早."""
        self._credits_session = session

    async def _progress(self, agent: str, status: str, message: str, percent: int) -> None:
        """上报进度给外部持久化回调."""
        event = {
            "agent": agent,
            "status": status,
            "message": message,
            "percent": percent,
            "stage": self.state_machine.get_state(),
            "timestamp": datetime.utcnow().isoformat(),
        }
        self.state_machine.context.setdefault("progress_events", []).append(event)
        self.state_machine.context["progress_percent"] = percent
        self.state_machine.context["current_agent"] = agent
        self.state_machine.context["current_message"] = message
        if self.progress_callback:
            await self.progress_callback({
                "status": self.state_machine.get_state(),
                "context": self.state_machine.context,
            })

    async def run(self) -> None:
        log.info("project_orchestrator_start", project_id=self.project_id)
        self.state_machine.send("START")
        await self._progress("ceo", "running", "CEO 正在理解你的需求并制定项目策略", 5)

        # P1-4: 把 cost_tracker 设到 contextvar, 让 LLMClient 自动 record
        from app.agent.cost_tracker import (
            set_current_cost_tracker,
            reset_current_cost_tracker,
            CostHardLimitExceeded,
        )
        _cost_token = set_current_cost_tracker(self.cost_tracker)

        try:
            # credits guard — 额度用完直接 paused, 不 raise
            try:
                await guard_llm_call(self._credits_session, self.org_id)
            except CreditsExhausted as e:
                log.warning("orchestrator_credits_exhausted", org_id=self.org_id)
                self.state_machine.context["credits_exhausted"] = True
                self.state_machine.context["paused_reason"] = str(e)
                self.state_machine.current_state = "paused"  # 不走 ERROR,保持 clean exit
                await self._progress("system", "paused", f"额度不足，项目已暂停：{e}", 0)
                return

            # 1. CEO 制定策略
            await self._run_ceo()

            # 2. PM 写 PRD (Level 1 fallback 会重跑这一步)
            prd = await self._run_pm()
            self.state_machine.send("PRD_DONE", prd)
            await self._progress("pm", "done", "PM 已完成 PRD，进入开发阶段", 28)

            # 2.5. Design Agent 生成视觉规范
            await self._run_design(prd)

            # 3-5. Develop + Test 闭环 (带 retry + fallback)
            await self._run_develop_test_loop(prd)

            # 6. Ops 生成部署配置
            await self._progress("ops", "running", "Ops Agent 正在生成部署配置和运行说明", 94)
            ops = OpsAgent(self.project_id, {"files": self.files})
            await ops.run()
            self.files = ops.get_files()
            deploy_url = ops.get_deploy_url()
            self.state_machine.send("DEPLOYED", deploy_url)
            await self._progress("ops", "done", "Ops Agent 已生成部署配置", 98)

            # 7. Learning / 完成
            self.state_machine.send("LEARNING_DONE")
            if self.state_machine.context.get("fallback_used"):
                await self._progress("system", "done", "项目生成完成（使用了兜底模板，可运行但非定制）", 100)
            else:
                await self._progress("system", "done", "项目生成完成，可以查看代码文件", 100)

        except Exception as e:
            # P1-4: CostHardLimitExceeded 是 abort 信号, 给用户友好 error
            if isinstance(e, CostHardLimitExceeded):
                user_msg = (
                    f"项目 LLM 成本超过硬上限 (${self.cost_tracker.total_usd:.2f} > "
                    f"${settings.cost_hard_limit_usd:.2f}),已自动 abort。"
                    f"可能原因: LLM 持续修复不收敛 / 提示词有 bug。请联系管理员检查。"
                )
                log.error("project_cost_hard_limit_abort", project_id=self.project_id, error=str(e))
                self.state_machine.send("ERROR", user_msg)
                await self._progress("system", "failed", user_msg, 100)
            else:
                log.error("project_orchestrator_error", project_id=self.project_id, error=str(e))
                self.state_machine.send("ERROR", str(e))
                await self._progress("system", "failed", f"项目生成失败：{e}", 100)
            reset_current_cost_tracker(_cost_token)
            raise
        else:
            # 正常完成也要 reset
            reset_current_cost_tracker(_cost_token)
            raise

    async def _run_ceo(self) -> None:
        await self._progress("ceo", "running", "CEO 正在理解你的需求并制定项目策略", 5)
        ceo = CeoAgent(self.project_id, {
            "user_idea": self.user_idea,
            "workflow_plan": self.workflow_plan,
        })
        await ceo.run()
        await self._progress("ceo", "done", "CEO 已制定项目策略", 12)

    async def _run_pm(self) -> str:
        await self._progress("pm", "running", "PM 正在把一句话需求扩展为 PRD", 18)
        pm = PMAgent(self.project_id, {
            "user_idea": self.user_idea,
            "workflow_plan": self.workflow_plan,
        })
        await pm.run()
        return pm.get_prd()

    async def _run_design(self, prd: str) -> None:
        """Design Agent: 从 PRD 生成 design_spec.json, 供 Frontend Agent 消费."""
        await self._progress("design", "running", "Design Agent 正在生成视觉规范", 30)
        design = DesignAgent(self.project_id, {
            "prd": prd,
            "user_idea": self.user_idea,
        })
        await design.run()
        self.design_spec = design.get_design_spec()
        # design_spec.json 已经写入 design.files["design_spec.json"]
        for k, v in design.get_files().items():
            self.files[k] = v
        if self.design_spec:
            mood = self.design_spec.get("mood", "?")
            primary = self.design_spec.get("palette", {}).get("primary", "?")
            await self._progress("design", "done", f"Design Agent 已生成视觉规范 (mood={mood}, primary={primary})", 35)
        else:
            await self._progress("design", "done", "Design Agent 使用默认视觉规范", 35)

    async def _run_develop_test_loop(self, prd: str) -> None:
        """Develop + Test 闭环: 生成 → 验证 → 失败带 feedback 重试 → stuck 触发 fallback.

        Backend 和 Frontend 并行执行:
        - 入口用 predict_api_contract 从 PRD 预测契约,Frontend 先用 predicted contract 生成
        - Backend 完成后拿到 actual contract,reconcile_fetch_urls 机械修正 Frontend fetch URL
        - Test 通过则进入 Ops;失败则反馈到对应 agent 重试

        Stage 2 增强: test 失败时优先用 tool-based repair (局部 patch) 而不是整段重抽。
        repair 写磁盘 → reload 回 self.files → 重跑 test_agent。MAX_REPAIR_PASSES=3。
        """
        orchestrator_retry = RetryState()
        level1_done = False
        feedback: dict[str, str] = {"backend": "", "frontend": ""}
        # 每次循环都并行 backend + frontend (省一半时间);首轮用 predicted contract,后续用 actual contract
        # Stage 2: repair pass 计数器 (per agent, 防单边过度修)
        repair_passes: dict[str, int] = {"backend": 0, "frontend": 0}
        MAX_REPAIR_PASSES = 3
        # NEW-1 fix: repair pass 成功后, 下一次循环应该只重跑 test_agent,
        # 不能重抽 Backend/Frontend — 否则 LLM 重新生成会引入新错, 死循环 (todo 项目实测 8 小时卡死)
        skip_regen = False

        while True:
            # 每次循环都并行 backend + frontend
            run_parallel = True

            # ---------- 准备 contract ----------
            # 首轮 (self.api_contract=None) 用 predicted contract 给 Frontend 抢先启动;
            # 后续重试时 Backend 已经跑过、self.api_contract 是 actual,Frontend 直接用 actual.
            # NEW-1 fix: skip_regen=True 时跳过 regen, 直接进 test (复用了 self.api_contract 和 self.files)
            predicted_contract = None
            if not self.api_contract and not skip_regen:
                predicted_contract = await self._predict_or_default_contract(prd)

            # ---------- Run Backend + Frontend (NEW-1 fix: skip_regen 时跳过) ----------
            if not skip_regen:
                await self._progress("backend", "running", "Backend Agent 正在设计 API 和数据模型", 38)
                await self._progress("frontend", "running", "Frontend Agent 正在根据 PRD 和设计规范生成界面", 62)

                backend = BackendAgent(self.project_id, {
                    "prd": prd,
                    "user_idea": self.user_idea,
                    "feedback": feedback["backend"],
                })
                # Frontend:首轮用 predicted,后续用 actual — predicted or self.api_contract 自动选 actual (若已存在)
                frontend = FrontendAgent(self.project_id, {
                    "prd": prd,
                    "user_idea": self.user_idea,
                    "api_contract": predicted_contract or self.api_contract,
                    "design_spec": self.design_spec,
                    "feedback": feedback["frontend"],
                })

                # 永远并行 backend + frontend — asyncio.gather 等两边都完成 (省一半时间)
                backend_task = asyncio.create_task(backend.run())
                frontend_task = asyncio.create_task(frontend.run())
                await asyncio.gather(backend_task, frontend_task)

                self._merge_files(backend.get_files(), "backend")
                self._merge_files(frontend.get_files(), "frontend")
            else:
                log.info("skip_regen_after_repair", project_id=self.project_id,
                         msg="repair pass 成功, 跳过后端/前端重抽, 直接重跑 test_agent 验证")
                await self._progress("test", "running",
                    "修复后重新验证 (跳过 backend/frontend 重抽)", 82)

            # 用 Backend 的 actual contract 覆盖 predicted
            self.api_contract = backend.get_api_contract()
            if self.api_contract and "backend/api_contract.json" not in self.files:
                self.files["backend/api_contract.json"] = json.dumps(self.api_contract, indent=2, ensure_ascii=False)

            backend_stuck = backend.get_retry_state().stuck
            frontend_stuck = frontend.get_retry_state().stuck
            self.state_machine.send("BACKEND_DONE")
            self.state_machine.send("FRONTEND_DONE")

            # 用 Backend actual contract 机械对齐 Frontend fetch URL (永远并行, 永远 reconcile)
            if self.api_contract:
                self._reconcile_frontend_with_actual_contract()

            # Stage 2: 把 self.files 同步到磁盘,给 tools 用
            self._sync_files_to_disk()

            await self._progress("backend", "done", "Backend Agent 已生成后端 API 和数据库模型", 52)
            await self._progress("frontend", "done", "Frontend Agent 已生成页面和组件", 74)

            # ---------- Run Test ----------
            await self._progress("test", "running", "Test Agent 正在静态 + 动态 + 交互验证项目", 82)
            test = TestAgent(self.project_id, {
                "files": self.files,
                "api_contract": self.api_contract,
                "user_idea": self.user_idea,
                "design_spec": self.design_spec,
            })
            await test.run()
            self.files = test.get_files()

            if test.is_passed():
                self.state_machine.send("TESTS_PASS")
                await self._progress("test", "done", "Test Agent 验证通过", 88)
                return

            # ---------- Test failed — repair pass / retry / fallback ----------
            self.state_machine.send("TESTS_FAIL")
            result = test.get_result()
            err_blob = "\n".join(result.errors)
            log.warning(
                "test_failed",
                project_id=self.project_id,
                failed_agent=result.failed_agent,
                attempt=orchestrator_retry.attempts + 1,
                repair_passes=repair_passes.copy(),
                errors=result.errors[:3],
            )

            # ---------- Stage 2: 优先用工具局部修复 (不重抽) ----------
            tool_project_root = self._get_tool_project_root()
            signals_dicts = [s.__dict__ for s in result.failure_signals]
            target_agent = result.failed_agent if result.failed_agent in ("backend", "frontend") else None

            repair_attempted = False
            if (
                target_agent
                and signals_dicts
                and tool_project_root is not None
                and repair_passes[target_agent] < MAX_REPAIR_PASSES
                and not (backend_stuck if target_agent == "backend" else frontend_stuck)
            ):
                await self._progress("repair", "running",
                    f"用工具局部修复 {target_agent} (pass {repair_passes[target_agent] + 1}/{MAX_REPAIR_PASSES})", 83)
                repair_attempted = True
                repair_result = await self._run_repair_pass(target_agent, tool_project_root, signals_dicts)
                repair_passes[target_agent] += 1

                # 关键: 从磁盘 reload 最新的 self.files (agent 改了文件)
                self._reload_files_from_disk()

                if repair_result.get("success"):
                    log.info("repair_succeeded", project_id=self.project_id,
                             agent=target_agent, tools_used=repair_result.get("tools_used", 0))
                    await self._progress("repair", "done",
                        f"{target_agent} 修复成功 (用了 {repair_result.get('tools_used', 0)} 个 tool)", 84)
                    # 修好 — 走下一次 while 循环只重跑 test_agent, 不重抽 backend/frontend
                    # (NEW-1 fix: 重抽会引入新错 → 死循环)
                    feedback = {"backend": "", "frontend": ""}
                    skip_regen = True
                    continue
                else:
                    log.warning("repair_failed", project_id=self.project_id,
                                agent=target_agent, text=repair_result.get("text", "")[:200])
                    await self._progress("repair", "done",
                        f"{target_agent} 修复未成功,继续回退路径", 84)
                    # 不 continue,走下面 stuck / 重抽路径

            # ---------- Stuck / repair 用尽 / 无信号 → fallback 或重抽 ----------
            agent_stuck = backend_stuck or frontend_stuck
            orchestrator_can_retry = orchestrator_retry.should_retry(err_blob)
            if agent_stuck or not orchestrator_can_retry or not repair_attempted:
                if not level1_done:
                    log.info("level1_fallback_start", project_id=self.project_id,
                             agent_stuck=agent_stuck, orchestrator_stuck=orchestrator_retry.stuck)
                    prd = await self._run_level1_fallback(prd)
                    level1_done = True
                    self.state_machine.context["fallback_level"] = 1
                    orchestrator_retry = RetryState()
                    feedback = {"backend": "", "frontend": ""}
                    self.state_machine.context["backend_ready"] = False
                    self.state_machine.context["frontend_ready"] = False
                    repair_passes = {"backend": 0, "frontend": 0}
                    continue
                else:
                    log.warning("level2_fallback_start", project_id=self.project_id)
                    await self._run_level2_fallback()
                    self.state_machine.send("TESTS_PASS")
                    await self._progress("test", "done", "使用兜底模板完成生成", 88)
                    return

            if result.failed_agent in ("backend", "both"):
                feedback["backend"] = err_blob
            if result.failed_agent in ("frontend", "both"):
                feedback["frontend"] = err_blob
            # 重试时清掉另一边的 stale feedback,让它也以空 feedback 参与并行重抽
            if result.failed_agent == "backend":
                feedback["frontend"] = ""
            elif result.failed_agent == "frontend":
                feedback["backend"] = ""
            await self._progress("test", "running", f"验证失败,带 feedback 重试 (attempt {orchestrator_retry.attempts + 1})", 82)

    async def _predict_or_default_contract(self, prd: str) -> dict | None:
        """从 PRD 预测 API 契约,失败则用默认 CRUD fallback.

        返回的 contract 与 derive_api_contract 同 schema (含 mount_prefix + endpoints)。
        """
        await self._progress("contract", "running", "正在从 PRD 预测 API 契约以启用并行", 36)
        contract = await predict_api_contract(prd, mount_prefix="/api/v1")
        if contract:
            log.info("contract_predicted", project_id=self.project_id,
                     endpoints=len(contract.get("endpoints", [])),
                     source=contract.get("derived_from", "?"))
            await self._progress("contract", "done", f"已预测 {len(contract['endpoints'])} 个 API 端点", 37)
            return contract
        contract = get_default_crud_contract("items", mount_prefix="/api/v1")
        log.warning("contract_predicted_fallback", project_id=self.project_id, source="default_crud")
        await self._progress("contract", "done", "PRD 预测失败,使用默认 CRUD fallback", 37)
        return contract

    def _reconcile_frontend_with_actual_contract(self) -> None:
        """并行跑完后,用 Backend 实际 contract 机械对齐 Frontend 的 fetch URL.

        Frontend 是用 predicted contract 生成的,如果预测与实际不一致,
        reconcile_fetch_urls 会把所有不在 actual contract 里的 fetch 改写为最近的 endpoint。
        """
        page_key = "frontend/src/app/page.tsx"
        page = self.files.get(page_key, "")
        if not page or not self.api_contract:
            return
        new_page, rewritten = reconcile_fetch_urls(page, self.api_contract)
        if rewritten:
            self.files[page_key] = new_page
            log.info("frontend_reconciled_after_parallel", project_id=self.project_id,
                     rewritten_count=len(rewritten), sample=rewritten[:3])

    async def _run_level1_fallback(self, original_prd: str) -> str:
        """Level 1: 简化 PRD, 重跑 pipeline. 返回简化后的 PRD."""
        await self._progress("pm", "running", "PRD 过于复杂，简化后重试", 20)
        try:
            simplified = await simplify_prd(original_prd)
            log.info("level1_prd_simplified", project_id=self.project_id,
                     original_len=len(original_prd), simplified_len=len(simplified))
            await self._progress("pm", "done", "PRD 已简化", 28)
            return simplified
        except Exception as e:
            log.error("level1_simplify_failed", project_id=self.project_id, error=str(e))
            return original_prd

    async def _run_level2_fallback(self) -> None:
        """Level 2: 加载预验证的 fallback 模板, 强制通过."""
        log.warning("level2_fallback_loaded", project_id=self.project_id)
        self.files = load_fallback_files()
        self.api_contract = get_fallback_contract()
        self.state_machine.context["fallback_used"] = True
        self.state_machine.context["fallback_level"] = 2

    # ---- Stage 2: tool-based repair 配套方法 ----

    def _get_tool_project_root(self) -> "Path | None":
        """获取 tools 操作的项目根目录: generated-projects/projects/{pid}.

        即使目录不存在也返回 (tools 找不到会报错),让 caller 决定。
        """
        from pathlib import Path
        # backend/app/agent/project_orchestrator.py -> backend/
        backend_dir = Path(__file__).parent.parent.parent
        return backend_dir / "generated-projects" / "projects" / str(self.project_id)

    def _sync_files_to_disk(self) -> None:
        """把 self.files 写到 generated-projects/projects/{pid}/ 下,给 tools 用.

        跳过二进制文件(截图)、非源码文件。"""
        from pathlib import Path
        root = self._get_tool_project_root()
        if root is None:
            return
        try:
            root.mkdir(parents=True, exist_ok=True)
            synced = 0
            for path, content in self.files.items():
                if not isinstance(content, str):
                    continue
                if path.startswith("_screenshots/") or path == "TEST_REPORT.md":
                    continue
                if not (path.startswith("backend/") or path.startswith("frontend/")):
                    continue
                full = root / path
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text(content, encoding="utf-8")
                synced += 1
            log.info("sync_files_to_disk", project_id=self.project_id, synced=synced, root=str(root))
        except Exception as e:
            log.warning("sync_files_to_disk_failed", project_id=self.project_id, error=str(e))

    def _reload_files_from_disk(self) -> None:
        """从 generated-projects/projects/{pid}/ 把最新 backend/frontend 源码回灌到 self.files."""
        from pathlib import Path
        root = self._get_tool_project_root()
        if root is None or not root.exists():
            return
        reloaded = 0
        for src in root.rglob("*"):
            if not src.is_file():
                continue
            if any(part in src.parts for part in ("node_modules", ".next", "dist", ".git")):
                continue
            if src.suffix not in (".ts", ".tsx", ".js", ".jsx", ".json", ".mjs", ".css", ".prisma", ".html", ".md"):
                continue
            rel = src.relative_to(root).as_posix()
            try:
                self.files[rel] = src.read_text(encoding="utf-8", errors="replace")
                reloaded += 1
            except OSError:
                continue
        log.info("reload_files_from_disk", project_id=self.project_id, reloaded=reloaded)

    async def _run_repair_pass(
        self,
        target_agent: str,
        tool_project_root: "Path",
        failure_signals: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """调对应 agent 的 repair_with_tools,把 failure_signals 喂过去."""
        log.info("repair_pass_start", project_id=self.project_id,
                 agent=target_agent, signals=len(failure_signals))
        if target_agent == "backend":
            agent = BackendAgent(self.project_id, {
                "prd": "",
                "user_idea": self.user_idea,
                "mode": "repair",
                "tool_project_root": str(tool_project_root),
            })
            repair_result = await agent.repair_with_tools(tool_project_root, failure_signals)
        elif target_agent == "frontend":
            agent = FrontendAgent(self.project_id, {
                "prd": "",
                "user_idea": self.user_idea,
                "api_contract": self.api_contract,
                "design_spec": self.design_spec,
                "mode": "repair",
                "tool_project_root": str(tool_project_root),
            })
            repair_result = await agent.repair_with_tools(tool_project_root, failure_signals)
        else:
            repair_result = {"success": False, "history": [], "text": "no target agent", "tools_used": 0}

        # 记录 tool_history 到 orchestrator context,方便观测和调试
        self.state_machine.context.setdefault("tool_history", []).append({
            "agent": target_agent,
            "success": repair_result.get("success"),
            "tools_used": repair_result.get("tools_used", 0),
            "history": repair_result.get("history", []),
            "text": repair_result.get("text", "")[:500],
        })
        return repair_result

    def _merge_files(self, new_files: dict[str, str], subdir: str) -> None:
        """合并 agent 生成的文件, 先清掉旧 subdir 下的文件避免残留."""
        # 清掉旧的 subdir 文件 (e.g. 重跑 backend 时, 旧 backend/* 不要残留)
        stale = [k for k in self.files if k.startswith(subdir + "/")]
        for k in stale:
            del self.files[k]
        self.files.update(new_files)

    async def persist(self, session: AsyncSession) -> Project:
        """把生成的文件持久化到存储和数据库."""
        prefix = f"projects/{self.project_id}"
        for path, content in self.files.items():
            await self.storage.write(prefix, path, content)

        # 创建 artifact 记录
        artifacts = []
        for path, content in self.files.items():
            artifact = Artifact(
                project_id=self.project_id,
                path=path,
                type=self._infer_type(path),
                content=content if len(content) < 64000 else None,
            )
            artifacts.append(artifact)
        session.add_all(artifacts)

        project = await session.get(Project, self.project_id)
        if project:
            project.status = self.state_machine.get_state()
            # paused 状态不写 error 字段
            if project.status != "paused":
                project.deploy_url = self.state_machine.context.get("deploy_url")
            project.credits_used = self.credits_used
            # failed 状态: 把 errors[] 拼成 user-readable message 写到 project.error
            if project.status == "failed":
                errors = self.state_machine.context.get("errors", [])
                if errors:
                    # 最后一条 error 优先 (最新的), 加 phase 信息
                    last_err = errors[-1]
                    if isinstance(last_err, dict):
                        msg = last_err.get("message") or last_err.get("error") or str(last_err)
                        phase = last_err.get("phase", "")
                    else:
                        msg = str(last_err)
                        phase = ""
                    if phase:
                        project.error = f"[{phase}] {msg}"
                    else:
                        project.error = msg
                    # 截断到 2000 chars 防 DB 爆
                    if project.error and len(project.error) > 2000:
                        project.error = project.error[:2000] + "...(truncated)"
            # 持久化 fallback 标记到 context JSONB
            project_context = project.context or {}
            project_context["fallback_used"] = self.state_machine.context.get("fallback_used", False)
            # P1-4: 持久化 LLM cost summary (上线后做账单 / 限额 / 推荐模板)
            project_context["llm_cost"] = self.cost_tracker.summary()
            project_context["fallback_level"] = self.state_machine.context.get("fallback_level", 0)
            project_context["progress_percent"] = self.state_machine.context.get("progress_percent")
            project_context["paused_reason"] = self.state_machine.context.get("paused_reason")
            project.context = project_context
            # paused 状态也要持久化文件
            if self.files:
                project.storage_prefix = prefix
            if project.status in ("done", "failed"):
                project.completed_at = datetime.utcnow()
            await session.commit()
        return project

    def _infer_type(self, path: str) -> str:
        if path.endswith((".tsx", ".ts", ".jsx", ".js")):
            return "code"
        if path.endswith((".prisma", ".sql")):
            return "config"
        if path.endswith((".md", ".txt")):
            return "doc"
        if path.startswith("test") or "test" in path.lower():
            return "test"
        if path.endswith("Dockerfile") or path.endswith(".yml") or path.endswith(".yaml"):
            return "docker"
        return "other"

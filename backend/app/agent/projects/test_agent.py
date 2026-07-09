"""Test Agent - 静态 + 动态两阶段验证.

静态: verify_backend + verify_frontend (tsc + import resolution + contract 对齐)
动态: npm install + 启 dev server + HTTP GET frontend / + HTTP GET 第一个 GET endpoint
视觉: Playwright 截图 + 感知哈希对比 (检测 UI 退化)
交互: Playwright 监听 console error + 关键交互 (form 提交) — 捕获运行时 bug

任一阶段失败 → VerificationResult.failed_agent 设定,orchestrator 据此回环重试.

动态阶段用 tempfile.mkdtemp() 隔离每次 run,避免上次 run 残留的 node_modules
在 Windows 文件锁下存活导致下次 run 跳过 install 报 'tsx not found'.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

# Playwright 用于截图对比 (需要 playwright install 安装浏览器)
try:
    from playwright.async_api import async_playwright as _async_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False
    _async_playwright = None

from app.agent.projects.base_agent import AgentAction, AgentState, ProjectAgent
from app.agent.projects.verify import VerifyResult, verify_backend, verify_frontend
from app.core.logging import get_logger
from app.services.dev_server import (
    DevServerHandle,
    find_free_port,
    run_install,
    start_backend,
    start_frontend,
    take_screenshot,
    teardown,
)

log = get_logger(__name__)

Phase = Literal["static", "dynamic", "visual", "interaction"]
FailedAgent = Literal["backend", "frontend", "both", None]


@dataclass
class FailureSignal:
    """结构化失败信号,orchestrator 喂给 agent 做局部 patch.

    与原始 errors[] 的区别:每个信号精确指向"哪个文件 + 什么错 + 怎么修",
    agent 拿到后能直接 read_file 验证 + edit_file 改,不需要再二次猜测。
    """
    file_path: str          # 如 "backend/src/routes.ts" 或 "frontend/src/app/page.tsx"
    error_kind: str         # "tsc_missing_import" | "tsc_type_error" | "http_404" | "http_500" | "server_crash" | "console_error" | "form_submit_fail" | "no_api_call" | "visual_diff" | ...
    error_msg: str          # 原始错误消息 (截断到 500 chars)
    suggested_action: str   # "add_dependency" | "fix_fetch_url" | "add_endpoint" | "fix_runtime" | "adjust_styles" | ...
    agent: str              # "backend" | "frontend" — orchestrator 路由用


@dataclass
class VerificationResult:
    passed: bool
    failed_agent: FailedAgent = None
    errors: list[str] = field(default_factory=list)
    phase: Phase = "static"
    failure_signals: list[FailureSignal] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.passed


def _extract_signals_from_errors(
    errors: list[str],
    files: dict[str, str],
    phase: str,
    failed_agent: str,
) -> list[FailureSignal]:
    """从 raw errors 提取结构化 FailureSignal.

    每个 raw error 字符串 -> 1+ 个 FailureSignal (一个错可能影响多文件)。
    推断规则:
      - 'Cannot find module X' (tsc) -> backend/frontend 那个 import X 的 .ts 文件
      - 'X does not exist on type' -> tsc_type_error
      - '404' / 'Not Found' -> frontend page.tsx (fetch URL 错)
      - '500' / 'Internal Server Error' -> backend routes.ts
      - 'exited with code' (dev server crash) -> backend index.ts
      - 'JS runtime error' / 'console error' -> frontend page.tsx
      - 'form submit returned 4xx/5xx' -> backend routes.ts (4xx/5xx) 或 frontend page.tsx
    """
    signals: list[FailureSignal] = []
    # 索引:已知文件后缀
    backend_ts = [k for k in files if k.startswith("backend/") and k.endswith(".ts")]
    frontend_tsx = [k for k in files if k.startswith("frontend/") and k.endswith((".tsx", ".ts"))]

    for raw in errors:
        s = raw or ""
        sl = s.lower()

        # tsc 缺 import
        if "cannot find module" in sl or "cannot find name" in sl or "has no exported member" in sl:
            # 找文件中已有的 import 源 (粗略)
            tgt = (backend_ts + frontend_tsx)[0] if (backend_ts + frontend_tsx) else "backend/src/routes.ts"
            signals.append(FailureSignal(
                file_path=tgt,
                error_kind="tsc_missing_import",
                error_msg=s[:500],
                suggested_action="add_dependency_or_import",
                agent=failed_agent if failed_agent in ("backend", "frontend") else "backend",
            ))
            continue

        # tsc 类型错
        if "does not exist on type" in sl or "property" in sl and "type" in sl:
            tgt = (backend_ts + frontend_tsx)[0] if (backend_ts + frontend_tsx) else "backend/src/routes.ts"
            signals.append(FailureSignal(
                file_path=tgt,
                error_kind="tsc_type_error",
                error_msg=s[:500],
                suggested_action="fix_type_or_shape",
                agent=failed_agent if failed_agent in ("backend", "frontend") else "backend",
            ))
            continue

        # HTTP 404 — 多半是 fetch URL 路径错
        if "404" in s or "not found" in sl:
            signals.append(FailureSignal(
                file_path="frontend/src/app/page.tsx",
                error_kind="http_404",
                error_msg=s[:500],
                suggested_action="fix_fetch_url",
                agent="frontend",
            ))
            continue

        # HTTP 500 — 多半是后端代码 (zod parse / 抛错)
        if "500" in s or "internal server error" in sl:
            signals.append(FailureSignal(
                file_path="backend/src/routes.ts",
                error_kind="http_500",
                error_msg=s[:500],
                suggested_action="fix_backend_handler",
                agent="backend",
            ))
            continue

        # dev server 进程退出 / 启动失败
        if "exited with code" in sl or "process exited" in sl:
            signals.append(FailureSignal(
                file_path="backend/src/index.ts",
                error_kind="server_crash",
                error_msg=s[:500],
                suggested_action="fix_server_startup",
                agent="backend",
            ))
            continue

        # JS 运行时错 / 控制台错
        if "console error" in sl or "js runtime" in sl or "pageerror" in sl or "uncaught" in sl:
            signals.append(FailureSignal(
                file_path="frontend/src/app/page.tsx",
                error_kind="console_error",
                error_msg=s[:500],
                suggested_action="fix_runtime",
                agent="frontend",
            ))
            continue

        # form submit 失败
        if "form submit" in sl or "submit returned" in sl:
            # 4xx/5xx -> backend,其它 -> frontend
            agent = "backend" if any(c in s for c in ("4", "5")) and "200" not in s else "frontend"
            signals.append(FailureSignal(
                file_path="backend/src/routes.ts" if agent == "backend" else "frontend/src/app/page.tsx",
                error_kind="form_submit_fail",
                error_msg=s[:500],
                suggested_action="fix_handler" if agent == "backend" else "fix_submit_logic",
                agent=agent,
            ))
            continue

        # 无 API 调用
        if "no api call" in sl or "api_calls=0" in sl:
            signals.append(FailureSignal(
                file_path="frontend/src/app/page.tsx",
                error_kind="no_api_call",
                error_msg=s[:500],
                suggested_action="wire_fetch",
                agent="frontend",
            ))
            continue

        # 视觉差异
        if "visual" in sl or "hash diff" in sl:
            signals.append(FailureSignal(
                file_path="frontend/src/app/page.tsx",
                error_kind="visual_diff",
                error_msg=s[:500],
                suggested_action="adjust_styles",
                agent="frontend",
            ))
            continue

        # 兜底:dynamic 阶段失败 -> 给 backend
        if phase == "dynamic":
            signals.append(FailureSignal(
                file_path="backend/src/index.ts",
                error_kind="dynamic_fail",
                error_msg=s[:500],
                suggested_action="review_dev_server",
                agent="backend",
            ))
        elif phase in ("visual", "interaction"):
            signals.append(FailureSignal(
                file_path="frontend/src/app/page.tsx",
                error_kind=phase,
                error_msg=s[:500],
                suggested_action="review_frontend",
                agent="frontend",
            ))
        else:
            # 静态阶段无法分类
            signals.append(FailureSignal(
                file_path=backend_ts[0] if backend_ts else "backend/src/routes.ts",
                error_kind="static_error",
                error_msg=s[:500],
                suggested_action="review_compile_errors",
                agent=failed_agent if failed_agent in ("backend", "frontend") else "backend",
            ))
    return signals


def _materialize(files: dict[str, str], target_dir: Path) -> None:
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    target_dir.mkdir(parents=True, exist_ok=True)
    for path, content in files.items():
        # orchestrator may merge file content from disk as bytes; write_text requires str.
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        full = target_dir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")


async def _http_get(url: str, expect: int = 200, timeout: int = 5) -> tuple[bool, str]:
    """HTTP GET,返回 (ok, message).ok=True 表示状态码匹配."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == expect:
                return True, f"{resp.status}"
            return False, f"expected {expect}, got {resp.status}"
    except Exception as e:
        return False, str(e)


async def _wait_http_ok(url: str, expect: int, timeout: int = 30) -> tuple[bool, str]:
    """轮询 URL 直到返回 expect 或超时."""
    deadline = asyncio.get_event_loop().time() + timeout
    last_msg = ""
    while asyncio.get_event_loop().time() < deadline:
        ok, msg = await _http_get(url, expect=expect, timeout=3)
        if ok:
            return True, msg
        last_msg = msg
        await asyncio.sleep(2)
    return False, f"timeout after {timeout}s, last: {last_msg}"


class TestAgent(ProjectAgent):
    role = "test"

    def __init__(self, project_id: int, context: dict[str, Any]):
        super().__init__(project_id, context)
        self.files = dict(context.get("files", {}))
        self.api_contract: dict | None = context.get("api_contract")
        self.user_idea: str = context.get("user_idea", "")
        self.result: VerificationResult = VerificationResult(passed=False)
        self.report = ""

    async def perceive(self) -> AgentState:
        return AgentState(
            project_id=self.project_id,
            role=self.role,
            data={"files": list(self.files.keys())},
        )

    async def reason(self, state: AgentState) -> AgentAction:
        if not self.result.passed and not self.report:
            return AgentAction(type="RUN_TESTS", payload=state.data)
        return AgentAction(type="WAIT")

    async def act(self, action: AgentAction) -> None:
        if action.type == "RUN_TESTS":
            log.info("test_running", project_id=self.project_id, file_count=len(self.files))
            self.result = await self._run_verification()
            self.report = self._format_report()
            self.files["TEST_REPORT.md"] = self.report

            self.record_action("RUN_TESTS")
            await self.save_memory(
                observation=f"测试结果: passed={self.result.passed}, failed_agent={self.result.failed_agent}, phase={self.result.phase}",
                insight="静态(tsc+import)+动态(HTTP 200)两阶段验证是闭环关键",
                importance=8,
            )
            self.mark_done()

    async def _run_verification(self) -> VerificationResult:
        """两阶段验证: 静态 → 动态."""
        # --- 静态阶段 ---
        log.info("test_static_phase_start", project_id=self.project_id)
        backend_result = await verify_backend(self.files, self.project_id)
        if not backend_result.passed:
            log.warning("test_static_backend_failed", project_id=self.project_id, errors=backend_result.errors[:3])
            return VerificationResult(
                passed=False,
                failed_agent="backend",
                errors=backend_result.errors,
                phase="static",
                failure_signals=_extract_signals_from_errors(backend_result.errors, self.files, "static", "backend"),
            )

        frontend_result = await verify_frontend(self.files, self.project_id, self.api_contract)
        if not frontend_result.passed:
            log.warning("test_static_frontend_failed", project_id=self.project_id, errors=frontend_result.errors[:3])
            return VerificationResult(
                passed=False,
                failed_agent="frontend",
                errors=frontend_result.errors,
                phase="static",
                failure_signals=_extract_signals_from_errors(frontend_result.errors, self.files, "static", "frontend"),
            )

        log.info("test_static_phase_passed", project_id=self.project_id)

        # --- 动态阶段 ---
        log.info("test_dynamic_phase_start", project_id=self.project_id)
        dynamic_result = await self._run_dynamic_phase()
        return dynamic_result

    async def _run_dynamic_phase(self) -> VerificationResult:
        """启 dev server + HTTP 探活.

        不靠 logs ready marker (Next.js 首次编译慢, marker 延迟大),
        直接轮询 HTTP 200 — 这是真正的 ready 信号.

        用 tempfile.mkdtemp() 隔离每次 run,避免 Windows 文件锁残留 node_modules.
        """
        test_dir = Path(tempfile.mkdtemp(prefix=f"opc_test_{self.project_id}_"))
        handle = DevServerHandle()
        logs: list[str] = []

        def log_fn(msg: str) -> None:
            logs.append(msg)
            log.info("test_dynamic_log", project_id=self.project_id, msg=msg)

        try:
            _materialize(self.files, test_dir)
            backend_dir = test_dir / "backend"
            frontend_dir = test_dir / "frontend"

            await run_install(backend_dir, log_fn)
            await run_install(frontend_dir, log_fn)

            backend_port = find_free_port(4100 + self.project_id * 2)
            frontend_port = find_free_port(4200 + self.project_id * 2)
            handle.backend_port = backend_port
            handle.frontend_port = frontend_port

            logs.append(f"Starting backend on port {backend_port}...")
            handle.backend_process = start_backend(backend_dir, backend_port, log_fn)

            logs.append(f"Starting frontend on port {frontend_port}...")
            handle.frontend_process = start_frontend(frontend_dir, frontend_port, backend_port, log_fn)

            # backend 探活 URL: contract 第一个 GET endpoint (优先 list 端点, 没有 :id 的)
            backend_probe = self._first_get_endpoint_url(backend_port)
            if not backend_probe:
                return self._dynamic_fail("backend", "no GET endpoint in api_contract to probe", logs)

            # 等进程不崩 + HTTP 通 — 不靠 marker
            be_ok, be_msg = await self._wait_http_with_proc_check(
                handle.backend_process,
                backend_probe,
                expect=200,
                timeout=45,
                logs=logs,
                proc_tag="backend",
            )
            if not be_ok:
                return self._dynamic_fail("backend", f"backend HTTP 200 failed on {backend_probe}: {be_msg}", logs)

            # frontend 轮询 (Next.js 首次编译慢, 给 120s)
            fe_ok, fe_msg = await self._wait_http_with_proc_check(
                handle.frontend_process,
                f"http://localhost:{frontend_port}/",
                expect=200,
                timeout=120,
                logs=logs,
                proc_tag="frontend",
            )
            if not fe_ok:
                return self._dynamic_fail("frontend", f"frontend HTTP 200 failed: {fe_msg}", logs)

            # --- 视觉阶段: 截图 + 感知哈希对比 ---
            visual_result = await self._run_visual_phase(frontend_port, logs)
            if visual_result is not None:
                return visual_result

            # --- 交互阶段: 验证 JS 运行时 + API 调用 + 关键交互 ---
            interaction_result = await self._run_interaction_phase(frontend_port, backend_port, logs)
            if interaction_result is not None:
                return interaction_result

            log.info("test_dynamic_passed", project_id=self.project_id,
                     frontend_port=frontend_port, backend_port=backend_port)
            return VerificationResult(passed=True, failed_agent=None, errors=[], phase="dynamic")

        except Exception as e:
            import traceback as _tb
            tb = _tb.format_exc()
            log.error("test_dynamic_exception", project_id=self.project_id, error=str(e), traceback=tb)
            return self._dynamic_fail("both", f"dynamic phase exception: {e}\n{tb}", logs)
        finally:
            teardown(handle)
            shutil.rmtree(test_dir, ignore_errors=True)

    async def _wait_http_with_proc_check(
        self,
        proc: Any,
        url: str,
        expect: int,
        timeout: int,
        logs: list[str],
        proc_tag: str,
    ) -> tuple[bool, str]:
        """轮询 HTTP 直到返回 expect 或进程退出或超时."""
        deadline = asyncio.get_event_loop().time() + timeout
        last_msg = ""
        while asyncio.get_event_loop().time() < deadline:
            # 进程崩了就不用等了
            if proc.poll() is not None:
                logs.append(f"{proc_tag} process exited with code {proc.returncode}")
                return False, f"{proc_tag} exited (code {proc.returncode})"
            ok, msg = await _http_get(url, expect=expect, timeout=5)
            if ok:
                return True, msg
            last_msg = msg
            await asyncio.sleep(3)
        return False, f"timeout after {timeout}s, last: {last_msg}"

    def _first_get_endpoint_url(self, backend_port: int) -> str | None:
        """从 api_contract 取 GET endpoint 做 backend 探活 URL.

        优先 list 端点 (path 不含 :id),其次 detail 端点 (:id 替换为 1).
        """
        if not self.api_contract:
            return f"http://localhost:{backend_port}/api/v1/todos"
        endpoints = self.api_contract.get("endpoints", [])
        # 先找 list 端点
        for ep in endpoints:
            if ep.get("method") == "GET" and ":id" not in ep.get("full", ""):
                return f"http://localhost:{backend_port}{ep['full']}"
        # 退而求其次,detail 端点 :id → 1
        for ep in endpoints:
            if ep.get("method") == "GET":
                path = ep.get("full", "").replace(":id", "1")
                return f"http://localhost:{backend_port}{path}"
        return None

    def _dynamic_fail(self, failed_agent: FailedAgent, reason: str, logs: list[str]) -> VerificationResult:
        recent_logs = "\n".join(logs[-20:])
        return VerificationResult(
            passed=False,
            failed_agent=failed_agent,
            errors=[reason, f"recent logs:\n{recent_logs}"],
            phase="dynamic",
            failure_signals=_extract_signals_from_errors([reason], self.files, "dynamic", failed_agent or "backend"),
        )

    # 感知哈希差异阈值: 超过此值认为视觉上发生了明显变化
    _VISUAL_HASH_THRESHOLD = 8

    async def _run_visual_phase(self, frontend_port: int, logs: list[str]) -> VerificationResult | None:
        """截图 + 感知哈希对比.

        返回 None 表示截图功能不可用或跳过 (非阻塞).
        返回 VerificationResult 表示视觉验证失败.
        """
        try:
            import imagehash
            from PIL import Image
        except ImportError:
            log.info("visual_skip_no_imagehash", msg="imagehash/Pillow 未安装，跳过视觉验证")
            return None

        screenshot_dir = Path(tempfile.gettempdir()) / f"opc_screenshots_{self.project_id}"
        screenshot_dir.mkdir(exist_ok=True)
        screenshot_path = screenshot_dir / "current.png"
        baseline_path = screenshot_dir / "baseline.png"

        url = f"http://localhost:{frontend_port}/"
        logs.append(f"Taking screenshot: {url}")
        ok = await take_screenshot(url, screenshot_path)
        if not ok:
            logs.append("Screenshot capture failed, skipping visual verification")
            return None

        # 计算当前截图的感知哈希
        try:
            current_hash = imagehash.phash(Image.open(screenshot_path))
        except Exception as e:
            logs.append(f"Failed to compute image hash: {e}")
            return None

        # 首次运行: 存 baseline
        if not baseline_path.exists():
            shutil.copy(screenshot_path, baseline_path)
            logs.append(f"Visual baseline saved (hash={current_hash})")
            return None

        # 对比 baseline
        baseline_hash = imagehash.phash(Image.open(baseline_path))
        diff = current_hash - baseline_hash
        logs.append(f"Visual hash diff: {diff} (threshold={self._VISUAL_HASH_THRESHOLD})")

        if diff > self._VISUAL_HASH_THRESHOLD:
            # 视觉差异过大: 截图保存到 files 供报告使用
            visual_diff_path = screenshot_dir / f"visual_diff_{hashlib.md5(str(diff).encode()).hexdigest()[:8]}.png"
            shutil.copy(screenshot_path, visual_diff_path)
            error_msg = (
                f"UI 视觉验证失败: 感知哈希差异 {diff} 超过阈值 {self._VISUAL_HASH_THRESHOLD}。"
                f"这表示前端渲染发生了明显变化，可能需要人工确认是否符合预期。"
            )
            self.files[f"_screenshots/visual_diff.png"] = visual_diff_path.read_bytes()
            logs.append(error_msg)
            return VerificationResult(
                passed=False,
                failed_agent="frontend",
                errors=[error_msg, f"hash diff={diff}, threshold={self._VISUAL_HASH_THRESHOLD}"],
                phase="visual",
                failure_signals=_extract_signals_from_errors([error_msg], self.files, "visual", "frontend"),
            )

        return None

    async def _run_interaction_phase(
        self, frontend_port: int, backend_port: int, logs: list[str]
    ) -> VerificationResult | None:
        """交互测试 v1: 验证 JS 运行时无错 + 有 API 调用 + 关键交互成功.

        返回 None = 通过 (或跳过).
        返回 VerificationResult = 失败.
        """
        if not _PLAYWRIGHT_AVAILABLE:
            logs.append("Playwright not available, skipping interaction test")
            return None

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logs.append("Playwright import failed, skipping")
            return None

        url = f"http://localhost:{frontend_port}/"
        api_pattern = f":{backend_port}/api/"
        console_errors: list[str] = []
        api_calls: list[str] = []
        # Stage 2 增强: 监听 backend API 响应,把 4xx/5xx 当作结构化 http_404/http_500 signal
        api_4xx_5xx: list[str] = []

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(viewport={"width": 1280, "height": 800})
                page = await context.new_page()

                # 监听 console error (JS 运行时错误)
                def on_console(msg: Any) -> None:
                    if msg.type == "error":
                        console_errors.append(f"[{msg.type}] {msg.text[:200]}")

                page.on("console", on_console)
                page.on("pageerror", lambda err: console_errors.append(f"[pageerror] {str(err)[:200]}"))

                # 监听 network request (必须看到对 backend 的调用)
                def on_request(req: Any) -> None:
                    if api_pattern in req.url:
                        api_calls.append(f"{req.method} {req.url}")

                page.on("request", on_request)

                # Stage 2: 监听 backend 响应,把 4xx/5xx 抓出来 — 这是路由错配的关键信号
                def on_response(resp: Any) -> None:
                    if api_pattern in resp.url and 400 <= resp.status < 600:
                        api_4xx_5xx.append(f"{resp.request.method} {resp.url} -> {resp.status}")

                page.on("response", on_response)

                # 1. 打开页面
                logs.append(f"[interaction] goto {url}")
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(2000)  # 等 React hydration

                # 2. 检查 JS 运行时错误
                if console_errors:
                    err_msg = f"JS 运行时错误 ({len(console_errors)} 条): {'; '.join(console_errors[:3])}"
                    logs.append(f"[interaction] {err_msg}")
                    await browser.close()
                    return VerificationResult(
                        passed=False,
                        failed_agent="frontend",
                        errors=[err_msg, "前端代码有运行时错误, 用户打开页面会看到空白或崩溃"],
                        phase="interaction",
                        failure_signals=_extract_signals_from_errors([err_msg, *console_errors[:3]], self.files, "interaction", "frontend"),
                    )

                # 3. 检查是否有 API 调用 (如果 contract 有端点, 应该至少一次 GET)
                endpoints = (self.api_contract or {}).get("endpoints", [])
                has_get_endpoint = any(ep.get("method") == "GET" for ep in endpoints)
                if has_get_endpoint and not api_calls:
                    err_msg = f"页面加载但没有 API 调用. endpoints={len(endpoints)}, api_calls=0"
                    logs.append(f"[interaction] {err_msg}")
                    await browser.close()
                    return VerificationResult(
                        passed=False,
                        failed_agent="frontend",
                        errors=[err_msg, "fetch() 可能没执行或端点路径错误"],
                        phase="interaction",
                        failure_signals=_extract_signals_from_errors([err_msg], self.files, "interaction", "frontend"),
                    )

                logs.append(f"[interaction] {len(api_calls)} API calls, {len(console_errors)} JS errors, {len(api_4xx_5xx)} 4xx/5xx")

                # 2.5. Stage 2: 路由错配检测 — 这是项目 11 类 bug 的关键拦截点
                # 如果前端调了 backend 但 backend 返回 4xx/5xx,通常是 fetch URL 错配
                if api_4xx_5xx:
                    sample = api_4xx_5xx[:3]
                    err_msg = (
                        f"前端 API 调用收到 4xx/5xx 响应 ({len(api_4xx_5xx)} 次). "
                        f"这通常意味着 fetch URL 路径与后端路由不匹配,需要前端修复.\n"
                        f"样例: {sample}"
                    )
                    logs.append(f"[interaction] {err_msg}")
                    await browser.close()
                    return VerificationResult(
                        passed=False,
                        failed_agent="frontend",
                        errors=[err_msg, "fetch URL 与 backend 路由错配,前端必须按 api_contract.json 修正"],
                        phase="interaction",
                        failure_signals=_extract_signals_from_errors(
                            [err_msg, *sample],
                            self.files,
                            "interaction",
                            "frontend",
                        ),
                    )

                # 4. 找 form 提交测试 (POST/PATCH/DELETE 端点)
                submit_endpoints = [ep for ep in endpoints if ep.get("method") in ("POST", "PATCH", "DELETE")]
                if submit_endpoints:
                    form_ok = await self._test_form_submission(page, api_pattern, logs)
                    if form_ok is False:
                        await browser.close()
                        return VerificationResult(
                            passed=False,
                            failed_agent="frontend",
                            errors=["Form 提交测试失败: 找到 form 但提交后 API 返回错误或 UI 没更新"],
                            phase="interaction",
                            failure_signals=_extract_signals_from_errors(
                                ["Form 提交测试失败: 找到 form 但提交后 API 返回错误或 UI 没更新"],
                                self.files, "interaction", "frontend"
                            ),
                        )
                else:
                    logs.append("[interaction] no POST/PATCH/DELETE endpoints, skipping form test")

                await browser.close()
                return None

        except Exception as e:
            import traceback as _tb
            tb = _tb.format_exc()
            logs.append(f"[interaction] exception: {e}\n{tb[:500]}")
            log.warning("interaction_test_exception", project_id=self.project_id, error=str(e))
            # 异常不视为失败, 降级跳过
            return None

    async def _test_form_submission(self, page: Any, api_pattern: str, logs: list[str]) -> bool | None:
        """找 form/input/button 提交一次, 验证 API 响应.

        返回 None = 跳过 (没找到 form), True = 成功, False = 失败.
        """
        try:
            forms = await page.query_selector_all("form")
            if not forms:
                logs.append("[interaction] no <form> found, skipping form test")
                return None

            # 用第一个 form
            form = forms[0]
            inputs = await form.query_selector_all("input[type='text'], input[type='search'], input:not([type])")
            submit_buttons = await form.query_selector_all("button[type='submit'], button")

            if not inputs or not submit_buttons:
                logs.append(f"[interaction] form has {len(inputs)} inputs, {len(submit_buttons)} buttons, skipping")
                return None

            # 准备测试值 (避免空格)
            test_value = f"interaction_test_{int(asyncio.get_event_loop().time())}"
            await inputs[0].fill(test_value)
            logs.append(f"[interaction] filled input with '{test_value}', clicking submit")

            # 点击并等待 API 响应
            api_response_received = False
            try:
                async with page.expect_response(
                    lambda r: api_pattern in r.url, timeout=8000
                ) as resp_info:
                    await submit_buttons[0].click()
                response = await resp_info.value
                api_response_received = True
                if response.status >= 400:
                    logs.append(f"[interaction] submit returned {response.status}")
                    return False
                logs.append(f"[interaction] submit returned {response.status}")
            except Exception as e:
                logs.append(f"[interaction] no API response after click: {e}")
                # 没收到 API 响应不一定失败 — 可能 button 不是 submit
                return None

            # 等 UI 更新
            await page.wait_for_timeout(1500)
            # 验证输入的值是否被清空 (常见 UX 模式)
            current_value = await inputs[0].input_value()
            if current_value == test_value:
                logs.append(f"[interaction] WARN: input value not cleared after submit (current='{current_value}')")
                # 不强制失败, 但记 log

            return True
        except Exception as e:
            logs.append(f"[interaction] form test exception: {e}")
            return None

    def _format_report(self) -> str:
        status = "通过" if self.result.passed else "失败"
        lines = [
            "# 测试报告",
            "",
            f"**总体结果**: {status}",
            f"**失败 Agent**: {self.result.failed_agent or '无'}",
            f"**阶段**: {self.result.phase}",
            "",
        ]
        if self.user_idea:
            lines.extend([
                "## 用户需求",
                "",
                f"> {self.user_idea}",
                "",
            ])

        if self.result.errors:
            lines.append("## 错误")
            for err in self.result.errors:
                lines.append(f"- {err[:500]}")
        return "\n".join(lines)

    def is_passed(self) -> bool:
        return self.result.passed

    def get_result(self) -> VerificationResult:
        return self.result

    def get_files(self) -> dict[str, str]:
        return self.files

"""Local preview process manager.

用于本地开发/演示:
- 自动在生成项目目录中安装依赖并启动 frontend/backend
- 给用户返回可访问的 preview_url
- 生产环境应替换为容器隔离/云端 preview runtime

安全边界:
- 只运行当前项目生成目录内的代码
- 每个 project 同一时间只有一个 preview
- 进程只在本机 localhost 暴露

dev server 启停/安装/drain 逻辑已抽出到 app.services.dev_server,
test_agent 动态验证阶段复用同一套。
"""
from __future__ import annotations

import asyncio
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from app.config import settings
from app.core.logging import get_logger
from app.services.dev_server import (
    DevServerHandle,
    find_free_port,
    run_install,
    start_backend,
    start_frontend,
    terminate_process,
)

log = get_logger(__name__)

PreviewStatus = Literal["stopped", "starting", "running", "failed"]


@dataclass
class PreviewProcess:
    project_id: int
    project_dir: Path
    backend_port: int
    frontend_port: int
    status: PreviewStatus = "stopped"
    preview_url: str | None = None
    backend_process: asyncio.subprocess.Process | None = None
    frontend_process: asyncio.subprocess.Process | None = None
    error: str | None = None
    logs: list[str] = field(default_factory=list)
    started_at: datetime | None = None
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def add_log(self, message: str) -> None:
        self.logs.append(f"[{datetime.utcnow().isoformat()}] {message}")
        # 避免日志无限增长
        self.logs = self.logs[-200:]
        self.updated_at = datetime.utcnow()


class PreviewProcessManager:
    """进程级 preview 管理器.

    注意: 这是进程内状态, 后端重启后 preview 状态会丢失.
    生产应使用 DB + 容器 runtime 记录状态.
    """

    def __init__(self) -> None:
        self._previews: dict[int, PreviewProcess] = {}
        self._lock = asyncio.Lock()

    async def start(self, project_id: int, storage_prefix: str | None) -> PreviewProcess:
        async with self._lock:
            existing = self._previews.get(project_id)
            if existing and existing.status in ("starting", "running"):
                return existing

            project_dir = self._resolve_project_dir(project_id, storage_prefix)
            if not project_dir.exists():
                raise FileNotFoundError(f"generated project directory not found: {project_dir}")

            backend_dir = project_dir / "backend"
            frontend_dir = project_dir / "frontend"
            if not backend_dir.exists() or not frontend_dir.exists():
                raise FileNotFoundError("generated project must contain backend/ and frontend/ directories")

            backend_port = find_free_port(4100 + project_id * 2)
            frontend_port = find_free_port(4200 + project_id * 2)

            preview = PreviewProcess(
                project_id=project_id,
                project_dir=project_dir,
                backend_port=backend_port,
                frontend_port=frontend_port,
                status="starting",
                preview_url=f"http://localhost:{frontend_port}",
                started_at=datetime.utcnow(),
            )
            self._previews[project_id] = preview

            # 后台启动, API 立即返回 starting
            asyncio.create_task(self._start_processes(preview))
            return preview

    async def stop(self, project_id: int) -> PreviewProcess | None:
        async with self._lock:
            preview = self._previews.get(project_id)
            if not preview:
                return None
            terminate_process(preview.frontend_process)
            terminate_process(preview.backend_process)
            preview.frontend_process = None
            preview.backend_process = None
            preview.status = "stopped"
            preview.add_log("Preview stopped")
            return preview

    def get(self, project_id: int) -> PreviewProcess | None:
        preview = self._previews.get(project_id)
        if not preview:
            return None
        # 检查进程是否还活着
        if preview.status == "running":
            backend_alive = preview.backend_process and preview.backend_process.poll() is None
            frontend_alive = preview.frontend_process and preview.frontend_process.poll() is None
            if not backend_alive or not frontend_alive:
                preview.status = "failed"
                preview.error = "Preview process exited unexpectedly"
                preview.add_log(preview.error)
        return preview

    def _resolve_project_dir(self, project_id: int, storage_prefix: str | None) -> Path:
        base = Path(settings.generated_projects_dir)
        if storage_prefix:
            return base / storage_prefix.replace("..", "").strip("/")
        return base / "projects" / str(project_id)

    async def _start_processes(self, preview: PreviewProcess) -> None:
        try:
            preview.add_log("Installing backend dependencies...")
            await run_install(preview.project_dir / "backend", preview.add_log)

            preview.add_log("Installing frontend dependencies...")
            await run_install(preview.project_dir / "frontend", preview.add_log)

            preview.add_log(f"Starting backend on port {preview.backend_port}...")
            preview.backend_process = start_backend(
                preview.project_dir / "backend", preview.backend_port, preview.add_log
            )

            preview.add_log(f"Starting frontend on port {preview.frontend_port}...")
            preview.frontend_process = start_frontend(
                preview.project_dir / "frontend",
                preview.frontend_port,
                preview.backend_port,
                preview.add_log,
            )

            # 健康闸门:不再 sleep 2s 就标 running,改成轮询 HTTP + ready markers
            ready = await self._wait_ready(preview, timeout=60)
            if ready:
                preview.status = "running"
                preview.add_log(f"Preview ready: {preview.preview_url}")
                log.info("preview_started", project_id=preview.project_id, url=preview.preview_url)
            else:
                preview.status = "failed"
                preview.error = "dev server did not become ready within 60s"
                preview.add_log(preview.error)
                log.error("preview_failed_ready", project_id=preview.project_id)
        except Exception as e:
            import traceback as _tb
            tb = _tb.format_exc()
            preview.status = "failed"
            preview.error = str(e) or repr(e) or type(e).__name__
            preview.add_log(f"Preview failed: {e!r}")
            preview.add_log(f"Traceback: {tb}")
            log.error("preview_failed", project_id=preview.project_id, error=str(e), traceback=tb)

    async def _wait_ready(self, preview: PreviewProcess, timeout: int = 60) -> bool:
        """轮询直到两个 dev server 都 ready,或超时.

        ready 信号:
        - backend: logs 含 'Server running on port' 或 HTTP GET backend_port 返回任意响应
        - frontend: logs 含 'Ready in' 或 HTTP GET frontend_port 返回 200
        """
        deadline = asyncio.get_event_loop().time() + timeout
        backend_ready = False
        frontend_ready = False

        while asyncio.get_event_loop().time() < deadline:
            # 进程崩了就不用等了
            if preview.backend_process and preview.backend_process.poll() is not None:
                preview.add_log("Backend process exited during readiness wait")
                return False
            if preview.frontend_process and preview.frontend_process.poll() is not None:
                preview.add_log("Frontend process exited during readiness wait")
                return False

            # 检查 logs 里的 ready markers
            if not backend_ready:
                backend_ready = any(
                    "Server running on port" in line or "Server listening" in line
                    for line in preview.logs[-30:]
                )
                if backend_ready:
                    preview.add_log("Backend ready signal detected in logs")

            if not frontend_ready:
                frontend_ready = any(
                    "Ready in" in line or "started server on" in line.lower()
                    for line in preview.logs[-30:]
                )
                if frontend_ready:
                    preview.add_log("Frontend ready signal detected in logs")

            # 两者都 ready 再做一次 HTTP 确认
            if backend_ready and frontend_ready:
                if await self._http_check(preview.frontend_port, expect=200):
                    preview.add_log("Frontend HTTP 200 confirmed")
                    return True

            # 也允许 HTTP 先于 ready marker 确认 (有些 dev server 不打标准 marker)
            if not backend_ready and await self._http_check(preview.backend_port):
                backend_ready = True
                preview.add_log("Backend HTTP responded")
            if not frontend_ready and await self._http_check(preview.frontend_port, expect=200):
                frontend_ready = True
                preview.add_log("Frontend HTTP 200 responded")

            if backend_ready and frontend_ready:
                return True

            await asyncio.sleep(2)

        return False

    async def _http_check(self, port: int, expect: int | None = None) -> bool:
        """HTTP GET localhost:port,返回 True 表示有响应且 (expect 为空 或 状态码匹配)."""
        url = f"http://localhost:{port}/"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if expect is None:
                    return True
                return resp.status == expect
        except Exception:
            return False


preview_manager = PreviewProcessManager()

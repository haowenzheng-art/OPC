"""Dev server shared logic — preview manager 和 test agent 共用.

抽出原因:
- preview.py 需要启动 dev server 给用户看
- test agent 动态验证阶段也需要启动 dev server 验证 HTTP 200
- 两者用同样的 install/start/drain/terminate 逻辑,避免重复实现

log_fn 是 callable[[str], None],调用方传 PreviewProcess.add_log 或自己的 sink。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Playwright 用于截图对比 (需要 playwright install 安装浏览器)
try:
    from playwright.async_api import async_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False
    async_playwright = None

from app.core.logging import get_logger

log = get_logger(__name__)

# Windows 上 npm 实际是 npm.cmd,create_subprocess_exec / Popen 不自动找 .cmd
NPM_BIN = shutil.which("npm") or "npm"


@dataclass
class DevServerHandle:
    """运行中的 dev server 句柄,持有进程和端口."""
    backend_process: subprocess.Popen | None = None
    frontend_process: subprocess.Popen | None = None
    backend_port: int = 0
    frontend_port: int = 0

    def alive(self) -> tuple[bool, bool]:
        """返回 (backend_alive, frontend_alive)."""
        b = self.backend_process is not None and self.backend_process.poll() is None
        f = self.frontend_process is not None and self.frontend_process.poll() is None
        return b, f


async def run_install(
    cwd: Path,
    log_fn: Callable[[str], None] | None = None,
    max_attempts: int = 3,
) -> None:
    """在 cwd 跑 npm install,带重试(Windows 上 prisma/zod 常因文件锁失败)."""
    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    if (cwd / "node_modules").exists():
        _log(f"Dependencies already installed: {cwd.name}")
        return

    last_output = ""
    for attempt in range(1, max_attempts + 1):
        _log(f"Installing {cwd.name} dependencies (attempt {attempt}/{max_attempts})...")
        process = await asyncio.create_subprocess_exec(
            NPM_BIN,
            "install",
            "--no-audit",
            "--no-fund",
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await process.communicate()
        output = stdout.decode(errors="ignore")[-4000:]
        last_output = output
        if process.returncode == 0:
            _log(output.strip() or f"npm install completed for {cwd.name}")
            return
        _log(f"npm install attempt {attempt} failed (exit {process.returncode}), cleaning partial node_modules...")
        shutil.rmtree(cwd / "node_modules", ignore_errors=True)

    raise RuntimeError(f"npm install failed in {cwd} after {max_attempts} attempts: {last_output}")


def start_backend(
    backend_dir: Path,
    port: int,
    log_fn: Callable[[str], None] | None = None,
) -> subprocess.Popen:
    """启动 Express backend (tsx src/index.ts),通过 PORT 环境变量传端口."""
    env = os.environ.copy()
    env["PORT"] = str(port)
    env["DATABASE_URL"] = "file:./dev.db"
    proc = subprocess.Popen(
        [NPM_BIN, "run", "dev"],
        cwd=str(backend_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    drain_output(proc, log_fn, "backend")
    return proc


def start_frontend(
    frontend_dir: Path,
    port: int,
    backend_port: int,
    log_fn: Callable[[str], None] | None = None,
) -> subprocess.Popen:
    """启动 Next.js frontend,通过 NEXT_PUBLIC_API_URL 指向 backend."""
    env = os.environ.copy()
    env["NEXT_PUBLIC_API_URL"] = f"http://localhost:{backend_port}/api/v1"
    proc = subprocess.Popen(
        [NPM_BIN, "run", "dev", "--", "--port", str(port)],
        cwd=str(frontend_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    drain_output(proc, log_fn, "frontend")
    return proc


def drain_output(
    proc: subprocess.Popen,
    log_fn: Callable[[str], None] | None,
    tag: str,
) -> None:
    """起 daemon 线程读 dev server stdout,防 PIPE 缓冲区写满导致进程挂起."""
    if log_fn is None:
        return

    def reader() -> None:
        try:
            for line in iter(proc.stdout.readline, ""):
                log_fn(f"[{tag}] {line.rstrip()}")
        except Exception:
            pass

    threading.Thread(target=reader, daemon=True).start()


def terminate_process(process: subprocess.Popen | None) -> None:
    """优雅 terminate,杀整个进程树 (npm 会 spawn tsx/next 子进程,只杀 npm 会留 orphan)."""
    if not process:
        return
    if process.poll() is None:
        pid = process.pid
        try:
            if os.name == "nt":
                # Windows: taskkill /T /F 杀整个进程树
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(pid)],
                    capture_output=True,
                    timeout=10,
                )
            else:
                process.terminate()
                process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        # 确认进程已死
        try:
            process.wait(timeout=3)
        except Exception:
            pass


def find_free_port(start: int) -> int:
    """从 start 开始找空闲端口,最多试 200 个."""
    port = start
    while port < start + 200:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("localhost", port)) != 0:
                return port
        port += 1
    raise RuntimeError("no free port available")


def teardown(handle: DevServerHandle) -> None:
    """停掉 handle 里的所有进程, 然后用端口二次保险清理残留.

    Windows 上 taskkill /T /F 偶尔漏掉 child 进程 (Next.js dev 的子 worker), 导致
    dev_server_handle 报"backend_process=None" 但端口仍被占用, 下次 find_free_port
    撞端口失败或下次启 dev server 报 EADDRINUSE.

    二次保险: 用 handle.backend_port / handle.frontend_port 拿到 PID 强杀.
    """
    terminate_process(handle.frontend_process)
    terminate_process(handle.backend_process)
    handle.frontend_process = None
    handle.backend_process = None

    # 二次保险: 通过端口查 PID, 杀掉任何残留的监听者
    if handle.backend_port:
        kill_orphan_on_port(handle.backend_port, "backend")
    if handle.frontend_port:
        kill_orphan_on_port(handle.frontend_port, "frontend")


def kill_orphan_on_port(port: int, tag: str = "unknown") -> int:
    """用 netstat 查 port 上的 PID, 然后 taskkill /F /T 杀进程树.

    返回杀掉的 PID 数量. Windows 专用 (用 netstat + taskkill).
    主要给 teardown() 二次保险用, 也给上层 (e.g. preview.py) 调.

    不抛异常 — best effort.
    """
    if os.name != "nt":
        return 0
    try:
        out_bytes = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, timeout=5,
        ).stdout
        # netstat 在中文 Windows 上输出 GBK, 用 errors='replace' 容错
        out = out_bytes.decode("utf-8", errors="replace") if out_bytes else ""
    except Exception as e:
        log.warning("netstat_failed", port=port, error=str(e))
        return 0

    # 解析 netstat 输出, 找 LISTENING 状态且本地端口匹配的 PID
    pids: set[int] = set()
    for line in out.splitlines():
        line = line.strip()
        if "LISTENING" not in line:
            continue
        # 格式: TCP    0.0.0.0:5432    0.0.0.0:0    LISTENING    1234
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            local_addr = parts[1]
            pid_str = parts[4]
            if ":" not in local_addr:
                continue
            local_port = int(local_addr.rsplit(":", 1)[1])
            if local_port != port:
                continue
            pid = int(pid_str)
            if pid > 0:
                pids.add(pid)
        except (ValueError, IndexError):
            continue

    killed = 0
    for pid in pids:
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                capture_output=True, timeout=5,
            )
            killed += 1
            log.info("orphan_killed", port=port, tag=tag, pid=pid)
        except Exception as e:
            log.warning("orphan_kill_failed", port=port, tag=tag, pid=pid, error=str(e))
    if killed == 0:
        log.debug("no_orphan_on_port", port=port, tag=tag)
    return killed


async def take_screenshot(url: str, output_path: Path, timeout: int = 15) -> bool:
    """用 Playwright 截取页面截图,返回是否成功.

    依赖: playwright 浏览器已安装 (playwright install chromium).
    """
    if not _PLAYWRIGHT_AVAILABLE:
        log.warning("playwright_not_available", msg="截图对比功能不可用，请 pip install playwright && playwright install chromium")
        return False

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1280, "height": 800})
            await page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
            await page.screenshot(path=str(output_path), full_page=True)
            await browser.close()
            log.info("screenshot_captured", url=url, path=str(output_path))
            return True
    except Exception as e:
        log.warning("screenshot_failed", url=url, error=str(e))
        return False

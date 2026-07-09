"""Project E2E Check - 给前端 /api/v1/projects/{id}/test/run 调用.

3 个 check:
  1. tsc --noEmit (frontend,if node_modules 已装)
  2. backend imports covered by package.json
  3. start dev server (backend + frontend) + HTTP 200 probe

每个 check 独立跑,一个失败不影响其他。返回结构化结果给前端展示。
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str
    duration_sec: float
    stderr: str | None = None


@dataclass
class E2ETestResult:
    passed: bool
    duration_sec: float
    checks: list[CheckResult]
    preview_url: str | None = None

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "duration_sec": self.duration_sec,
            "checks": [asdict(c) for c in self.checks],
            "preview_url": self.preview_url,
        }


def _get_project_dir(project_id: int) -> Path:
    """项目目录: backend/generated-projects/projects/{id}/

    跟 verify_preview_e2e.py / test_agent._materialize 用的同一路径。
    """
    return Path(__file__).parent.parent.parent / "generated-projects" / "projects" / str(project_id)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# ---------- Check 1: tsc ----------

def check_tsc(project_dir: Path) -> CheckResult:
    """Check 1: tsc --noEmit on frontend (if node_modules present)."""
    t0 = time.time()
    fe_dir = project_dir / "frontend"
    page = fe_dir / "src" / "app" / "page.tsx"
    if not page.exists():
        return CheckResult("tsc", False, "frontend/src/app/page.tsx 缺失", time.time() - t0)
    if not (fe_dir / "node_modules").exists():
        return CheckResult(
            "tsc", True,
            f"跳过 (node_modules 未安装,无 TypeScript 工具链) — page.tsx {page.stat().st_size} bytes",
            time.time() - t0,
        )
    npx_bin = shutil.which("npx") or "npx"
    try:
        r = subprocess.run(
            [npx_bin, "tsc", "--noEmit"],
            cwd=str(fe_dir), capture_output=True, text=True, timeout=120,
        )
        duration = time.time() - t0
        if r.returncode == 0:
            line_count = len(page.read_text(encoding="utf-8").splitlines())
            return CheckResult("tsc", True, f"frontend TypeScript 编译通过 (page.tsx {line_count} 行)", duration)
        return CheckResult("tsc", False, "tsc --noEmit 报错 (见 stderr)", duration, stderr=(r.stdout + r.stderr)[:3000])
    except subprocess.TimeoutExpired:
        return CheckResult("tsc", False, "tsc 超过 120s timeout", time.time() - t0)
    except Exception as e:
        return CheckResult("tsc", False, f"tsc 异常: {type(e).__name__}: {e}", time.time() - t0)


# ---------- Check 2: package.json 覆盖 ----------

_BUILTIN_MODULES = {
    "fs", "path", "http", "crypto", "os", "url", "util", "stream",
    "events", "buffer", "process", "child_process", "zlib", "assert",
    "querystring", "readline", "tls", "net", "dns", "cluster", "dgram",
}


def check_package_json_coverage(project_dir: Path) -> CheckResult:
    """Check 2: backend/src/**/*.ts 的所有非相对 import 都声明在 package.json."""
    t0 = time.time()
    be_dir = project_dir / "backend"
    pkg_path = be_dir / "package.json"
    if not pkg_path.exists():
        return CheckResult("package_json_coverage", False, "backend/package.json 缺失", time.time() - t0)
    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return CheckResult("package_json_coverage", False, f"package.json 不是合法 JSON: {e}", time.time() - t0)

    declared = set(pkg.get("dependencies", {}).keys()) | set(pkg.get("devDependencies", {}).keys())
    pattern = re.compile(r"""(?:import|export)[^'"]*?from\s*['"]([^'"]+)['"]""")
    missing: set[str] = set()
    for src in be_dir.glob("src/**/*.ts"):
        try:
            code = src.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in pattern.finditer(code):
            spec = m.group(1)
            if spec.startswith(".") or spec.startswith("/") or spec.startswith("node:"):
                continue
            parts = spec.split("/")
            pkg_name = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
            if pkg_name in _BUILTIN_MODULES:
                continue
            if pkg_name not in declared:
                missing.add(pkg_name)

    duration = time.time() - t0
    if missing:
        return CheckResult(
            "package_json_coverage", False,
            f"backend 引用了 {len(missing)} 个 package.json 没声明的包: {sorted(missing)}",
            duration,
        )
    return CheckResult("package_json_coverage", True, f"所有 import 都有声明 ({len(declared)} deps)", duration)


# ---------- Check 3: preview + HTTP 200 ----------

def _http_get_status(url: str, timeout: int = 5) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="ignore")[:500]
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return 0, str(e)


async def check_preview_http_200(project_dir: Path, base_port: int = 5200) -> CheckResult:
    """Check 3: 启 backend + frontend dev server,probe HTTP 200.

    端口从 base_port 起算,避开 live preview (4100/4200 段)。
    """
    from app.services.dev_server import (
        find_free_port, run_install, start_backend, start_frontend, terminate_process,
    )

    t0 = time.time()
    be_dir = project_dir / "backend"
    fe_dir = project_dir / "frontend"
    if not (be_dir / "package.json").exists() or not (fe_dir / "package.json").exists():
        return CheckResult("preview_http_200", False, "缺 backend 或 frontend 的 package.json", time.time() - t0)

    try:
        be_port = find_free_port(base_port)
        fe_port = find_free_port(base_port + 100)
    except RuntimeError as e:
        return CheckResult("preview_http_200", False, f"找不到空闲端口: {e}", time.time() - t0)

    be_proc = None
    fe_proc = None
    logs: list[str] = []

    def log_fn(msg: str) -> None:
        logs.append(msg)
        if len(logs) > 100:
            logs.pop(0)

    try:
        # npm install (如有缓存会很快)
        await run_install(be_dir, log_fn)
        await run_install(fe_dir, log_fn)

        be_proc = start_backend(be_dir, be_port, log_fn)
        fe_proc = start_frontend(fe_dir, fe_port, be_port, log_fn)

        # 等 backend ready (15 次 × 2s = 30s)
        backend_ok = False
        for _ in range(15):
            if be_proc.poll() is not None:
                return CheckResult("preview_http_200", False,
                                   f"backend 进程退出 (code {be_proc.returncode})",
                                   time.time() - t0, stderr="\n".join(logs[-20:]))
            status, _ = _http_get_status(f"http://localhost:{be_port}/api/v1/health", timeout=2)
            if status == 200:
                backend_ok = True
                break
            await asyncio.sleep(2)
        if not backend_ok:
            return CheckResult("preview_http_200", False,
                               f"backend 端口 {be_port} 30s 内未 ready",
                               time.time() - t0, stderr="\n".join(logs[-20:]))

        # 等 frontend ready (Next.js 首次编译慢,给 60 次 × 2s = 120s)
        fe_url = f"http://localhost:{fe_port}/"
        for _ in range(60):
            if fe_proc.poll() is not None:
                return CheckResult("preview_http_200", False,
                                   f"frontend 进程退出 (code {fe_proc.returncode})",
                                   time.time() - t0, stderr="\n".join(logs[-20:]))
            status, body = _http_get_status(fe_url, timeout=3)
            if status == 200 and "<html" in body.lower():
                duration = time.time() - t0
                return CheckResult(
                    "preview_http_200", True,
                    f"HTTP 200 (body {len(body)} chars), backend:{be_port} frontend:{fe_port}",
                    duration,
                )
            await asyncio.sleep(2)

        return CheckResult("preview_http_200", False,
                           f"frontend {fe_url} 120s 内未返回 200",
                           time.time() - t0, stderr="\n".join(logs[-30:]))
    except Exception as e:
        return CheckResult("preview_http_200", False,
                           f"启 dev server 异常: {type(e).__name__}: {e}",
                           time.time() - t0, stderr="\n".join(logs[-20:]))
    finally:
        if fe_proc:
            terminate_process(fe_proc)
        if be_proc:
            terminate_process(be_proc)


# ---------- 主入口 ----------

async def run_project_e2e_check(project_id: int) -> E2ETestResult:
    """对一个已 done 的项目跑 3 个 e2e check."""
    t0 = time.time()
    project_dir = _get_project_dir(project_id)
    if not project_dir.exists():
        return E2ETestResult(
            passed=False,
            duration_sec=time.time() - t0,
            checks=[CheckResult("project_exists", False, f"项目目录不存在: {project_dir}", time.time() - t0)],
        )

    log.info("e2e_check_start", project_id=project_id, dir=str(project_dir))

    # 串行跑 3 个 check (tsc 快,coverage 瞬时,preview 慢)
    c1 = check_tsc(project_dir)
    c2 = check_package_json_coverage(project_dir)
    c3 = await check_preview_http_200(project_dir)

    checks = [c1, c2, c3]
    passed = all(c.passed for c in checks)
    duration = time.time() - t0

    log.info(
        "e2e_check_done",
        project_id=project_id,
        passed=passed,
        duration_sec=round(duration, 1),
        check_results=[(c.name, c.passed, c.duration_sec) for c in checks],
    )

    return E2ETestResult(passed=passed, duration_sec=duration, checks=checks)

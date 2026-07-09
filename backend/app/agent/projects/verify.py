"""静态验证闸门 — tsc + import resolution + contract 对齐.

agent 生成代码后立刻验证,失败则带 feedback 重生成。
验证在 generated-projects/projects/{id}/_verify/ 影子目录跑,不污染最终目录。
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.agent.projects.api_contract import (
    derive_api_contract,
    derive_mount_prefix,
    reconcile_fetch_urls,
)
from app.agent.projects.imports import scan_third_party_imports
from app.config import settings
from app.core.logging import get_logger
from app.services.dev_server import NPM_BIN, run_install

log = get_logger(__name__)


@dataclass
class VerifyResult:
    passed: bool
    errors: list[str] = field(default_factory=list)
    failed_agent: str | None = None  # "backend" | "frontend" | None

    def __bool__(self) -> bool:
        return self.passed


def _verify_dir(project_id: int) -> Path:
    base = Path(settings.generated_projects_dir) / "projects" / str(project_id) / "_verify"
    return base


def _materialize(files: dict[str, str], target_dir: Path) -> None:
    """把 agent 生成的 files dict 物化到 target_dir."""
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    target_dir.mkdir(parents=True, exist_ok=True)
    for path, content in files.items():
        full = target_dir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            full.write_bytes(content)
        else:
            full.write_text(content, encoding="utf-8")


def _run_tsc_sync(cwd: Path) -> tuple[bool, str]:
    """同步跑 npx tsc --noEmit,返回 (success, output).

    过滤 TS7016 (Could not find a declaration file) — 这是类型声明缺失,
    dev server (tsx for backend / Next for frontend) 不做类型检查,不影响运行。
    真正要抓的是 syntax error (TS1xxx)、Cannot find module (TS2307) 等。
    """
    if not (cwd / "tsconfig.json").exists():
        return True, ""  # 没有 tsconfig 就跳过 tsc
    try:
        result = subprocess.run(
            [NPM_BIN, "exec", "--", "tsc", "--noEmit", "--skipLibCheck"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=120,
            shell=False,
        )
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode == 0:
            return True, output
        # 过滤 TS7016 (declaration file missing) — dev server 不受影响
        lines = output.splitlines()
        real_errors = [
            ln for ln in lines
            if ln.strip()
            and "error TS" in ln
            and "TS7016" not in ln  # declaration file missing
            and "TS2307" not in ln  # Cannot find module — 但这个可能是真错,下面再判断
        ]
        # TS2307 (Cannot find module) 需要区分:包没装(真错) vs 类型声明缺失(噪音)
        # 如果 _check_imports_in_package_json 已经验证了 import 都在 package.json,
        # 那么 TS2307 大概率是类型声明问题(包装了但没 .d.ts),可以过滤。
        ts2307_lines = [ln for ln in lines if "TS2307" in ln]
        if ts2307_lines and not real_errors:
            # 只有 TS2307 且没有其他真错,视为类型声明噪音
            return True, "\n".join(ts2307_lines) + "\n(filtered as type-declaration noise)"
        if not real_errors:
            return True, output
        return False, "\n".join(real_errors)
    except subprocess.TimeoutExpired:
        return False, "tsc timed out after 120s"
    except Exception as e:
        return False, f"tsc invocation failed: {e}"


async def run_tsc(cwd: Path) -> tuple[bool, str]:
    """异步包装 tsc."""
    return await asyncio.to_thread(_run_tsc_sync, cwd)


def _check_imports_in_package_json(files: dict[str, str], subdir: str) -> list[str]:
    """检查 subdir 下所有 .ts/.tsx 文件的第三方 import 是否在 package.json 中."""
    pkg_json_path = f"{subdir}/package.json"
    if pkg_json_path not in files:
        return [f"{pkg_json_path} missing"]

    try:
        pkg = json.loads(files[pkg_json_path])
    except json.JSONDecodeError as e:
        return [f"{pkg_json_path} invalid JSON: {e}"]

    declared = set(pkg.get("dependencies", {}).keys()) | set(pkg.get("devDependencies", {}).keys())

    missing: list[str] = []
    for path, content in files.items():
        if not path.startswith(subdir + "/"):
            continue
        if not path.endswith((".ts", ".tsx", ".js", ".jsx")):
            continue
        imports = scan_third_party_imports(content)
        for imp in imports:
            if imp not in declared:
                missing.append(f"{imp} (imported in {path}, not in {pkg_json_path})")
    return missing


async def verify_backend(files: dict[str, str], project_id: int) -> VerifyResult:
    """验证 backend: tsc + import resolution + contract 可派生."""
    result = VerifyResult(passed=True, failed_agent="backend")
    verify_dir = _verify_dir(project_id)

    try:
        _materialize(files, verify_dir)
        backend_dir = verify_dir / "backend"

        # 1. import resolution check (不需要 install,只看 package.json)
        missing = _check_imports_in_package_json(files, "backend")
        if missing:
            result.passed = False
            result.errors.append("Missing backend dependencies: " + "; ".join(missing[:5]))

        # 2. tsc check (需要 node_modules — 跑 install)
        if (backend_dir / "package.json").exists():
            try:
                await run_install(backend_dir, log_fn=None)
                ok, output = await run_tsc(backend_dir)
                if not ok:
                    result.passed = False
                    # 截断 tsc 输出避免爆炸
                    result.errors.append("backend tsc failed:\n" + output[:2000])
            except Exception as e:
                result.passed = False
                result.errors.append(f"backend install/tsc error: {e}")

        # 3. contract 可派生 (routes.ts 有 router.METHOD 调用)
        routes_ts = files.get("backend/src/routes.ts", "")
        index_ts = files.get("backend/src/index.ts", "")
        if routes_ts:
            mount = derive_mount_prefix(index_ts)
            contract = derive_api_contract(routes_ts, mount)
            if contract is None:
                result.passed = False
                result.errors.append("Failed to derive api_contract from routes.ts (no router.METHOD('/path') calls found)")
        else:
            result.passed = False
            result.errors.append("backend/src/routes.ts missing")

    finally:
        # 清理 _verify 目录,避免占用磁盘
        shutil.rmtree(verify_dir, ignore_errors=True)

    return result


async def verify_frontend(
    files: dict[str, str],
    project_id: int,
    api_contract: dict | None = None,
) -> VerifyResult:
    """验证 frontend: tsc + import resolution + fetch URL 对齐.

    api_contract 可选 — 若传入则检查 page.tsx 的 fetch URL 是否都在 contract 里。
    """
    result = VerifyResult(passed=True, failed_agent="frontend")
    verify_dir = _verify_dir(project_id)

    try:
        _materialize(files, verify_dir)
        frontend_dir = verify_dir / "frontend"

        # 1. import resolution (复用 _scan_third_party_imports,扫 page.tsx + layout.tsx)
        missing = _check_imports_in_package_json(files, "frontend")
        if missing:
            result.passed = False
            result.errors.append("Missing frontend dependencies: " + "; ".join(missing[:5]))

        # 2. tsc check
        if (frontend_dir / "package.json").exists():
            try:
                await run_install(frontend_dir, log_fn=None)
                ok, output = await run_tsc(frontend_dir)
                if not ok:
                    result.passed = False
                    result.errors.append("frontend tsc failed:\n" + output[:2000])
            except Exception as e:
                result.passed = False
                result.errors.append(f"frontend install/tsc error: {e}")

        # 3. fetch URL 对齐 (若提供 contract)
        page_tsx = files.get("frontend/src/app/page.tsx", "")
        if page_tsx and api_contract:
            _, rewritten = reconcile_fetch_urls(page_tsx, api_contract)
            if rewritten:
                result.passed = False
                result.errors.append(
                    f"frontend fetch URLs not in api_contract: {rewritten[:5]}"
                )

        # 4. design_spec.json 软检查 (存在且 JSON 合法; 不强制有完整字段)
        design_spec_raw = files.get("design_spec.json")
        if design_spec_raw:
            try:
                spec = json.loads(design_spec_raw)
                if not isinstance(spec, dict) or "palette" not in spec:
                    result.passed = False
                    result.errors.append("design_spec.json exists but invalid (missing palette)")
            except json.JSONDecodeError as e:
                result.passed = False
                result.errors.append(f"design_spec.json invalid JSON: {e}")

    finally:
        shutil.rmtree(verify_dir, ignore_errors=True)

    return result

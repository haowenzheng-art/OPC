"""test_fallback_template.py — Verify fallback_template loads cleanly + runs end-to-end.

Strategy: 3 layers of checks, each independent.

  Layer 1 (UNIT):    load_fallback_files() returns files that satisfy schema checks
  Layer 2 (E2E):     the loaded files can be materialized + run as a real todo app
  Layer 3 (ORCH):    _run_level2_fallback() flow works through ProjectOrchestrator

Run:  python test_fallback_template.py
      (independent — no env vars, no live OPC server required for layers 1/2)
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Chinese Windows defaults to gbk; force utf-8 for subprocesses so log lines don't crash.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

# Make sure we run from backend/ so 'app' module is importable.
BACKEND_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.projects.fallback import (  # noqa: E402
    _TEMPLATE_DIR,
    get_fallback_contract,
    load_fallback_files,
)

TEST_ROOT = BACKEND_ROOT / "test_fallback_out"
BACKEND = TEST_ROOT / "backend"
FRONTEND = TEST_ROOT / "frontend"
# ARTIFACTS lives outside TEST_ROOT so Layer 3 wiping TEST_ROOT doesn't kill the
# Layer 2 screenshot.
ARTIFACTS = BACKEND_ROOT / "test_fallback_artifacts"

REQUIRED_KEYS = [
    "backend/package.json",
    "backend/tsconfig.json",
    "backend/api_contract.json",
    "backend/prisma/schema.prisma",
    "backend/src/index.ts",
    "backend/src/db.ts",
    "backend/src/routes.ts",
    "frontend/package.json",
    "frontend/tsconfig.json",
    "frontend/next.config.mjs",
    "frontend/postcss.config.js",
    "frontend/tailwind.config.js",
    "frontend/src/app/layout.tsx",
    "frontend/src/app/page.tsx",
    "frontend/src/app/globals.css",
]

results: list[tuple[str, bool, str]] = []


# ---------- tiny harness ----------
def expect(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    icon = "[PASS]" if ok else "[FAIL]"
    line = f"  {icon} {name}"
    if detail and not ok:
        line += f"\n         {detail}"
    print(line)


def header(s: str) -> None:
    print(f"\n=== {s} ===")


# ---------- helpers ----------
def port_open(p: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", p), timeout=1):
            return True
    except OSError:
        return False


def http_ready(url: str, timeout_s: int = 60) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status < 500:
                    return True
        except (urllib.error.URLError, ConnectionResetError, OSError):
            pass
        time.sleep(0.5)
    return False


def free_port(port: int) -> None:
    if not port_open(port):
        return
    print(f"  freeing :{port}…")
    if sys.platform == "win32":
        try:
            # netstat -ano output is decoded with the OEM code page (cp936/cp437 on
            # Chinese Windows); using text=True can crash with UnicodeDecodeError.
            out_bytes = subprocess.check_output(["netstat", "-ano"])
            out = out_bytes.decode("mbcs", errors="replace")
            for line in out.splitlines():
                if f":{port} " in line and "LISTENING" in line:
                    pid = line.split()[-1]
                    if pid.isdigit():
                        subprocess.run(
                            ["taskkill", "/F", "/PID", pid],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
        except (subprocess.CalledProcessError, UnicodeDecodeError):
            pass
    time.sleep(1)


def write_files(files: dict[str, str], root: Path) -> None:
    for rel, content in files.items():
        full = root / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")


# ---------- Layer 1: unit ----------
def test_layer1_unit() -> None:
    header("Layer 1: load_fallback_files() unit checks")
    files = load_fallback_files()
    expect(
        "load_fallback_files returns >= 14 files",
        len(files) >= 14,
        f"got {len(files)} files",
    )
    for key in REQUIRED_KEYS:
        expect(f"contains {key}", key in files, "" if key in files else "missing key")

    # api_contract: PUT semantics
    contract = get_fallback_contract()
    expect("api_contract is a dict", isinstance(contract, dict))
    methods = {ep.get("method") for ep in contract.get("endpoints", [])}
    expect("api_contract includes GET /todos", "GET" in methods)
    expect("api_contract includes POST /todos", "POST" in methods)
    expect("api_contract includes PUT /todos/:id", "PUT" in methods)
    expect("api_contract includes DELETE /todos/:id", "DELETE" in methods)

    # routes.ts syntactic check
    routes_ts = files.get("backend/src/routes.ts", "")
    for snippet, label in [
        ("router.get('/todos'", "GET handler present"),
        ("router.post('/todos'", "POST handler present"),
        ("router.put('/todos/:id'", "PUT handler present"),
        ("router.delete('/todos/:id'", "DELETE handler present"),
        ("prisma.todo", "uses Prisma client"),
        ("z.object", "uses zod validation"),
    ]:
        expect(f"routes.ts {label}", snippet in routes_ts)

    # frontend page.tsx key features
    page = files.get("frontend/src/app/page.tsx", "")
    for snippet, label in [
        ("'use client'", "client component"),
        ("useState", "uses React state"),
        ("filter", "has filter logic"),
        ("localStorage", "has localStorage fallback"),
        ("fetch(`${API}", "calls backend API"),
        ("PUT", "uses PUT for updates"),
        ("DELETE", "uses DELETE"),
        ("data-testid={`filter-${f}`}", "filter tab testid template"),
        ("type Filter = 'all' | 'active' | 'completed'", "Filter type alias defined"),
        ("data-testid=\"clear-completed\"", "clear-completed testid"),
        ("data-testid=\"todo-input\"", "todo-input testid"),
    ]:
        expect(f"page.tsx {label}", snippet in page)

    # design_spec.json / slots.json present at root for future Stage 4 use
    spec_path = _TEMPLATE_DIR / "design_spec.json"
    slots_path = _TEMPLATE_DIR / "slots.json"
    expect("design_spec.json exists at root", spec_path.is_file())
    expect("slots.json exists at root", slots_path.is_file())
    if spec_path.is_file():
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        expect("design_spec has palette.primary", "primary" in spec.get("palette", {}))
        expect("design_spec has typography.h1", "h1" in spec.get("typography", {}))
        expect("design_spec has components.input", "input" in spec.get("components", {}))
        expect("design_spec has mood", "mood" in spec)

    # package.json deps sanity (every third-party import is declared)
    imports_declared = _check_imports_in_package_json(files)
    expect("backend imports covered by package.json", not imports_declared.get("backend"),
           "; ".join(imports_declared.get("backend", [])[:3]))


def _check_imports_in_package_json(files: dict[str, str]) -> dict[str, list[str]]:
    """Inline copy of verify._check_imports_in_package_json to avoid coupling this test to
    verify.py internals; verifies the same scan contract."""
    import re

    NODE_BUILTINS = {
        "assert", "async_hooks", "buffer", "child_process", "cluster", "console",
        "constants", "crypto", "dgram", "dns", "domain", "events", "fs", "http",
        "http2", "https", "inspector", "module", "net", "os", "path", "perf_hooks",
        "process", "punycode", "querystring", "readline", "repl", "stream",
        "string_decoder", "sys", "timers", "tls", "trace_events", "tty", "url",
        "util", "v8", "vm", "wasi", "worker_threads", "zlib",
    }
    IMPORT_RE = re.compile(r"""(?:import|export)[^'"]*?from\s*['"]([^'"]+)['"]""")

    out: dict[str, list[str]] = {}
    for sub in ("backend", "frontend"):
        sub_files = {k: v for k, v in files.items() if k.startswith(sub + "/")}
        pkg_json_key = f"{sub}/package.json"
        if pkg_json_key not in sub_files:
            out[sub] = ["package.json missing"]
            continue
        try:
            pkg = json.loads(sub_files[pkg_json_key])
        except json.JSONDecodeError as e:
            out[sub] = [f"invalid package.json: {e}"]
            continue
        declared = set(pkg.get("dependencies", {}).keys()) | set(pkg.get("devDependencies", {}).keys())
        missing = []
        for path, content in sub_files.items():
            if not path.endswith((".ts", ".tsx", ".js", ".jsx")):
                continue
            for m in IMPORT_RE.finditer(content):
                spec = m.group(1).strip()
                if spec.startswith(".") or spec.startswith("/") or spec.startswith("node:"):
                    continue
                root = spec.split("/")[0]
                if root in NODE_BUILTINS:
                    continue
                if spec.startswith("@"):
                    pkg_name = "/".join(spec.split("/")[:2])
                else:
                    pkg_name = root
                if pkg_name not in declared:
                    missing.append(f"{pkg_name} (in {path})")
        out[sub] = missing
    return out


# ---------- Layer 2: end-to-end ----------
def test_layer2_e2e() -> None:
    header("Layer 2: load → materialize → install → boot → HTTP 200")
    if TEST_ROOT.exists():
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    # Wipe stale artifacts dir from any earlier run so the screenshot is fresh.
    for old in ARTIFACTS.glob("fallback-app*.png"):
        old.unlink()

    files = load_fallback_files()
    write_files(files, TEST_ROOT)

    expect("backend dir materialized", (BACKEND / "package.json").is_file())
    expect("frontend dir materialized", (FRONTEND / "package.json").is_file())

    # ---- backend install + tsc ----
    print("\n  [backend] npm install…")
    try:
        subprocess.run(
            ["npm", "install", "--no-audit", "--no-fund", "--prefer-offline"],
            cwd=str(BACKEND),
            check=True,
            timeout=600,
            shell=True,  # Windows: npm.cmd is a batch file
        )
        expect("backend npm install", True)
    except subprocess.CalledProcessError as e:
        expect("backend npm install", False, str(e))
        return

    print("  [backend] npx prisma generate…")
    proc = subprocess.run(
        "npx prisma generate",
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        timeout=120,
        shell=True,
    )
    expect("backend prisma generate", proc.returncode == 0,
           ((proc.stdout or "") + (proc.stderr or ""))[:500])

    print("  [backend] npx tsc --noEmit…")
    proc = subprocess.run(
        "npx tsc --noEmit --skipLibCheck",
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        timeout=120,
        shell=True,
    )
    expect("backend tsc --noEmit", proc.returncode == 0,
           ((proc.stdout or "") + (proc.stderr or ""))[:500])

    # ---- frontend install + tsc ----
    print("\n  [frontend] npm install…")
    try:
        subprocess.run(
            ["npm", "install", "--no-audit", "--no-fund", "--prefer-offline"],
            cwd=str(FRONTEND),
            check=True,
            timeout=600,
            shell=True,
        )
        expect("frontend npm install", True)
    except subprocess.CalledProcessError as e:
        expect("frontend npm install", False, str(e))
        return

    print("  [frontend] npx tsc --noEmit…")
    proc = subprocess.run(
        "npx tsc --noEmit --skipLibCheck",
        cwd=str(FRONTEND),
        capture_output=True,
        text=True,
        timeout=120,
        shell=True,
    )
    expect("frontend tsc --noEmit", proc.returncode == 0,
           ((proc.stdout or "") + (proc.stderr or ""))[:500])

    # ---- boot backend + frontend ----
    free_port(3000)
    free_port(3001)

    backend_log = TEST_ROOT / "backend.log"
    frontend_log = TEST_ROOT / "frontend.log"

    CREATE_NEW_PG = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    print("\n  [boot] backend on :3001…")
    backend_proc = subprocess.Popen(
        "npx tsx src/index.ts",
        cwd=str(BACKEND),
        stdout=backend_log.open("w"),
        stderr=subprocess.STDOUT,
        env={**os.environ, "PORT": "3001"},
        shell=True,
        creationflags=CREATE_NEW_PG,
    )
    print("  [boot] frontend on :3000…")
    frontend_proc = subprocess.Popen(
        "npx next dev -p 3000",
        cwd=str(FRONTEND),
        stdout=frontend_log.open("w"),
        stderr=subprocess.STDOUT,
        env={**os.environ, "NEXT_PUBLIC_API_URL": "http://localhost:3001/api/v1"},
        shell=True,
        creationflags=CREATE_NEW_PG,
    )

    try:
        ok_backend = http_ready("http://localhost:3001/health", 30)
        ok_frontend = http_ready("http://localhost:3000", 90)
        expect("backend /health ready", ok_backend, "see backend.log")
        expect("frontend :3000 ready", ok_frontend, "see frontend.log")

        if ok_backend:
            # CRUD smoke against the live API
            post = urllib.request.urlopen(
                urllib.request.Request(
                    "http://localhost:3001/api/v1/todos",
                    data=json.dumps({"title": "Test from layer2"}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                ),
                timeout=10,
            )
            payload = json.loads(post.read())
            expect("POST /todos returned 201 + data.id", post.status == 201 and "id" in payload.get("data", {}))
            todo_id = payload.get("data", {}).get("id", "")

            if todo_id:
                put = urllib.request.urlopen(
                    urllib.request.Request(
                        f"http://localhost:3001/api/v1/todos/{todo_id}",
                        data=json.dumps({"completed": True}).encode(),
                        headers={"Content-Type": "application/json"},
                        method="PUT",
                    ),
                    timeout=10,
                )
                updated = json.loads(put.read())
                expect("PUT /todos/:id toggles completed", updated.get("data", {}).get("completed") is True,
                       json.dumps(updated)[:200])

                delete = urllib.request.urlopen(
                    urllib.request.Request(
                        f"http://localhost:3001/api/v1/todos/{todo_id}", method="DELETE"
                    ),
                    timeout=10,
                )
                expect("DELETE /todos/:id returns 200",
                       delete.status == 200)

        if ok_frontend:
            # Snap screenshot
            try:
                from playwright.sync_api import sync_playwright

                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    page = browser.new_context().new_page()
                    page.goto("http://localhost:3000", wait_until="networkidle")
                    page.wait_for_selector('[data-testid="todo-input"]', timeout=15_000)
                    page.fill('[data-testid="todo-input"]', "Item from screenshot")
                    page.click('[data-testid="add-btn"]')
                    page.wait_for_timeout(500)
                    shot = ARTIFACTS / "fallback-app.png"
                    page.screenshot(path=str(shot), full_page=True)
                    text = page.locator("body").inner_text()
                    browser.close()
                expect("fallback-app.png captured", shot.is_file(),
                       f"text contains item? {'item from screenshot' in text.lower()}")
            except Exception as e:  # noqa: BLE001
                expect("fallback-app.png captured", False, str(e)[:300])
    finally:
        for proc, name in ((backend_proc, "backend"), (frontend_proc, "frontend")):
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, ProcessLookupError, KeyboardInterrupt):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            print(f"  stopped {name}")


# ---------- Layer 3: orchestrator fallback path ----------
def test_layer3_orchestrator_fallback() -> None:
    header("Layer 3: ProjectOrchestrator._run_level2_fallback + preview path")
    try:
        from app.agent.project_orchestrator import ProjectOrchestrator
    except Exception as e:  # noqa: BLE001
        expect("import ProjectOrchestrator", False, str(e))
        return

    if TEST_ROOT.exists():
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    orch = ProjectOrchestrator(
        project_id=999_999,  # sentinel — never persisted
        user_idea="test fallback",
    )

    # 1. direct call to _run_level2_fallback
    print("  [orch] calling _run_level2_fallback()…")
    try:
        asyncio.run(orch._run_level2_fallback())
    except Exception as e:  # noqa: BLE001
        expect("_run_level2_fallback raised", False, str(e))
        return

    expect("context.fallback_used", orch.state_machine.context.get("fallback_used") is True)
    expect("context.fallback_level == 2", orch.state_machine.context.get("fallback_level") == 2)
    expect("orch.api_contract populated", isinstance(orch.api_contract, dict) and bool(orch.api_contract.get("endpoints")))
    expect("orch.files non-empty", len(orch.files) > 0)

    # 2. Materialize to disk in the same shape OPC uses
    write_files(orch.files, TEST_ROOT)
    print(f"  [orch] wrote {len(orch.files)} files to {TEST_ROOT}")

    backend_log = TEST_ROOT / "backend.log"
    free_port(3000)
    free_port(3001)
    CREATE_NEW_PG = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    # Install deps (same as a real run) + generate prisma client
    print("  [orch] npm install + prisma generate…")
    install_proc = subprocess.run(
        ["npm", "install", "--no-audit", "--no-fund", "--prefer-offline"],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        timeout=600,
        shell=True,
    )
    expect("orch-level3 backend npm install", install_proc.returncode == 0,
           ((install_proc.stdout or "") + (install_proc.stderr or ""))[:300])

    gen_proc = subprocess.run(
        "npx prisma generate",
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        timeout=120,
        shell=True,
    )
    expect("orch-level3 backend prisma generate", gen_proc.returncode == 0,
           ((gen_proc.stdout or "") + (gen_proc.stderr or ""))[:300])

    print("  [orch] booting backend on :3001…")
    backend_proc = subprocess.Popen(
        "npx tsx src/index.ts",
        cwd=str(BACKEND),
        stdout=backend_log.open("w"),
        stderr=subprocess.STDOUT,
        env={**os.environ, "PORT": "3001"},
        shell=True,
        creationflags=CREATE_NEW_PG,
    )

    try:
        ok = http_ready("http://localhost:3001/health", 30)
        expect("backend reachable after level2 fallback", ok, "see backend.log")
    finally:
        try:
            backend_proc.terminate()
            backend_proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, ProcessLookupError, KeyboardInterrupt):
            try:
                backend_proc.kill()
            except ProcessLookupError:
                pass


# ---------- entry ----------
def main() -> int:
    print(f"template dir: {_TEMPLATE_DIR}")
    test_layer1_unit()
    test_layer2_e2e()
    test_layer3_orchestrator_fallback()

    passed = sum(1 for _, ok, _ in results if ok)
    failed = [r for r in results if not r[1]]
    print("\n" + "=" * 60)
    print(f"  {passed}/{len(results)} checks passed")
    for name, ok, detail in failed:
        print(f"  FAIL: {name} — {detail}")
    print("=" * 60)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

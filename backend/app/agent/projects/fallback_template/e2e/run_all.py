"""run_all.py — boot backend + frontend, run e2e, clean up.

Single entry point for verifying the todo-app-v2 fallback template end-to-end:

    Step 1: backend   npm install
    Step 2: backend   prisma generate + tsc --noEmit + build
    Step 3: frontend  npm install + next build + tsc --noEmit
    Step 4: e2e       playwright smoke (start backend+frontend, run specs, kill)

Kills any leftover servers on :3000/:3001 first (siblings of the current project),
boots fresh, runs the script, then cleans up.
"""
from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # .../fallback_template
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
E2E_DIR = ROOT / "e2e"


def banner(step: str, msg: str) -> None:
    print(f"\n========== {step}: {msg} ==========")


def run(cmd: list[str], cwd: Path, timeout: int = 600) -> None:
    print(f"$ {' '.join(cmd)}  (cwd={cwd})")
    proc = subprocess.run(cmd, cwd=cwd, shell=False, timeout=timeout)
    if proc.returncode != 0:
        raise SystemExit(f"FAILED (exit={proc.returncode}): {' '.join(cmd)}")


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def http_ready(url: str, timeout_s: int = 60) -> bool:
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status < 500:
                    return True
        except (urllib.error.URLError, ConnectionResetError, OSError):
            pass
        time.sleep(0.5)
    return False


def free_port(port: int) -> None:
    """Best-effort: find process LISTENing on port (Windows-friendly via netstat)."""
    if not port_open("127.0.0.1", port):
        return
    print(f"  port {port} already bound — attempting to free…")
    if sys.platform == "win32":
        # netstat -ano gives PID, then taskkill /F /PID <pid>
        try:
            out = subprocess.check_output(
                ["netstat", "-ano", "-p", "TCP"], text=True, stderr=subprocess.STDOUT
            )
            for line in out.splitlines():
                if f":{port} " in line and "LISTENING" in line:
                    parts = line.split()
                    pid = parts[-1]
                    if pid.isdigit():
                        subprocess.run(
                            ["taskkill", "/F", "/PID", pid],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        print(f"  killed pid {pid} on :{port}")
        except subprocess.CalledProcessError:
            pass
    else:
        try:
            subprocess.run(["fuser", "-k", f"{port}/tcp"])
        except FileNotFoundError:
            pass


def main() -> int:
    print(f"root: {ROOT}")

    # ---- 0. free ports (don't kill siblings) ---------------------------------
    # We only kill processes bound to these ports; siblings like searchengine:8001
    # run on different ports and won't be touched.
    free_port(3000)
    free_port(3001)

    # ---- 1. backend install + build ------------------------------------------
    banner("Step 1", "backend npm install")
    run(["npm", "install", "--no-audit", "--no-fund"], cwd=BACKEND)

    banner("Step 2", "backend prisma generate + tsc --noEmit + tsc build")
    run(["npx", "prisma", "generate"], cwd=BACKEND)
    run(["npx", "tsc", "--noEmit", "--skipLibCheck"], cwd=BACKEND)
    run(["npx", "tsc"], cwd=BACKEND)

    # ---- 3. frontend install + build -----------------------------------------
    banner("Step 3", "frontend npm install + next build + tsc --noEmit")
    run(["npm", "install", "--no-audit", "--no-fund"], cwd=FRONTEND)
    # next build runs tsc-equivalent checks internally, but we also want explicit tsc
    run(["npx", "tsc", "--noEmit", "--skipLibCheck"], cwd=FRONTEND)

    # ---- 4. e2e ---------------------------------------------------------------
    banner("Step 4", "playwright e2e")
    run(["npm", "install", "--no-audit", "--no-fund"], cwd=E2E_DIR)
    # install chromium browser binary (idempotent)
    run(["npx", "playwright", "install", "chromium"], cwd=E2E_DIR, timeout=300)

    # boot backend and frontend
    backend_log = ROOT / "e2e" / "backend.log"
    frontend_log = ROOT / "e2e" / "frontend.log"
    backend_proc = subprocess.Popen(
        ["npx", "tsx", "src/index.ts"],
        cwd=BACKEND,
        stdout=backend_log.open("w"),
        stderr=subprocess.STDOUT,
        env={**os.environ, "PORT": "3001"},
    )
    frontend_proc = subprocess.Popen(
        ["npx", "next", "dev", "-p", "3000"],
        cwd=FRONTEND,
        stdout=frontend_log.open("w"),
        stderr=subprocess.STDOUT,
        env={**os.environ, "NEXT_PUBLIC_API_URL": "http://localhost:3001/api/v1"},
    )

    failed = False
    try:
        if not http_ready("http://localhost:3001/health", timeout_s=30):
            print("backend never became ready; check backend.log")
            failed = True
        elif not http_ready("http://localhost:3000", timeout_s=120):
            print("frontend never became ready; check frontend.log")
            failed = True
        else:
            run(["node", "e2e.mjs"], cwd=E2E_DIR)
    except SystemExit as e:
        failed = True
        print(f"e2e step failed: {e}")
    finally:
        for proc, name in ((backend_proc, "backend"), (frontend_proc, "frontend")):
            try:
                if sys.platform == "win32":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                else:
                    proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, ProcessLookupError, KeyboardInterrupt):
                proc.kill()
            print(f"  stopped {name}")

    print("\n=========================================")
    print("ALL STEPS PASSED" if not failed else "SOME STEPS FAILED")
    print("=========================================")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

"""Background baseline runner - 把 baseline 真正 detach 到独立进程.

用 Windows job object + DETACHED_PROCESS 让进程不依附于 shell session,
即使 shell 重启, baseline 也会继续跑.
"""
import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(r"C:\Users\19802\Desktop\ClaudeCodeTest\opc\backend")
LOG_PATH = BACKEND_DIR / "logs" / "baseline_bg.log"
ERR_PATH = BACKEND_DIR / "logs" / "baseline_bg.err.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

# Windows: DETACHED_PROCESS (0x00000008) | CREATE_NEW_PROCESS_GROUP (0x00000200)
DETACHED = 0x00000008 | 0x00000200

print(f"Starting baseline in background...")
print(f"Log: {LOG_PATH}")
print(f"Err: {ERR_PATH}")

with open(LOG_PATH, "w", buffering=1) as log, open(ERR_PATH, "w", buffering=1) as err:
    p = subprocess.Popen(
        [str(BACKEND_DIR / ".venv" / "Scripts" / "python.exe"),
         str(BACKEND_DIR / "verify_baseline.py"), "--quick"],
        cwd=str(BACKEND_DIR),
        stdout=log, stderr=err,
        creationflags=DETACHED,
        close_fds=True,
    )
    print(f"PID: {p.pid}")
    # detach: 不 wait, 让 parent 退出

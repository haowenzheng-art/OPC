"""真正独立的 detached baseline runner.
用 detach_runner 的 DETACHED_PROCESS + CREATE_NO_WINDOW 真 detach.
"""
import os
import subprocess
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent
DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000


def main() -> int:
    log_dir = backend_dir / "logs"
    log_dir.mkdir(exist_ok=True)

    out_log = log_dir / "baseline_detached.out.log"
    err_log = log_dir / "baseline_detached.err.log"

    env = os.environ.copy()
    env["OPC_BASELINE_TIMEOUT"] = "1500"
    env["OPC_DISABLE_BILLING"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    args = [
        sys.executable,
        str(backend_dir / "verify_baseline.py"),
        "--quick",
    ]

    if sys.platform == "win32":
        proc = subprocess.Popen(
            args,
            stdout=open(out_log, "ab", buffering=0),
            stderr=open(err_log, "ab", buffering=0),
            stdin=subprocess.DEVNULL,
            env=env,
            cwd=str(backend_dir),
            creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
            close_fds=True,
        )
    else:
        proc = subprocess.Popen(
            args,
            stdout=open(out_log, "ab", buffering=0),
            stderr=open(err_log, "ab", buffering=0),
            stdin=subprocess.DEVNULL,
            env=env,
            cwd=str(backend_dir),
            start_new_session=True,
            close_fds=True,
        )
    print(f"Baseline detached PID: {proc.pid}")
    print(f"Log: {out_log}")
    print(f"Err: {err_log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""真正独立的 frontend dev server 启动.
"""
import os
import subprocess
import sys
from pathlib import Path

DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000


def main() -> int:
    frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
    log_dir = frontend_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    out_log = log_dir / "next.out.log"
    err_log = log_dir / "next.err.log"

    env = os.environ.copy()
    env["NODE_ENV"] = "development"
    env["FORCE_COLOR"] = "0"
    env["NO_COLOR"] = "1"
    env["CI"] = "1"

    # 用绝对路径 npx.cmd, 避开 cd 路径问题
    # Windows .cmd 必须通过 cmd.exe 包装运行
    npx_cmd = frontend_dir / "node_modules" / ".bin" / "npx.cmd"
    args = [
        "cmd.exe",
        "/c",
        str(npx_cmd),
        "next",
        "dev",
        "--webpack",
        "--port",
        "3000",
    ]

    proc = subprocess.Popen(
        args,
        cwd=str(frontend_dir),
        stdout=open(out_log, "ab", buffering=0),
        stderr=open(err_log, "ab", buffering=0),
        stdin=subprocess.DEVNULL,
        env=env,
        creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
        close_fds=True,
    )
    print(f"Frontend dev server detached PID: {proc.pid}")
    print(f"Log: {out_log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

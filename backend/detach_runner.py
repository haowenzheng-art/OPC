"""真正独立的进程 detach (CREATE_NO_WINDOW + DETACHED_PROCESS).
绕开 PowerShell bash tool 自动回收子进程的问题.
"""
import os
import subprocess
import sys


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: detach_runner.py <log_dir> <args...>", file=sys.stderr)
        return 1

    log_dir = sys.argv[1]
    os.makedirs(log_dir, exist_ok=True)
    args = sys.argv[2:]

    out_log = os.path.join(log_dir, "proc.out.log")
    err_log = os.path.join(log_dir, "proc.err.log")

    if sys.platform == "win32":
        DETACHED_PROCESS = 0x00000008
        CREATE_NO_WINDOW = 0x08000000
        proc = subprocess.Popen(
            args,
            stdout=open(out_log, "ab", buffering=0),
            stderr=open(err_log, "ab", buffering=0),
            stdin=subprocess.DEVNULL,
            creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
            close_fds=True,
        )
    else:
        proc = subprocess.Popen(
            args,
            stdout=open(out_log, "ab", buffering=0),
            stderr=open(err_log, "ab", buffering=0),
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )

    print(proc.pid)
    return 0


if __name__ == "__main__":
    sys.exit(main())

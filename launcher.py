"""OPC 一键启动器.

启动 backend (uvicorn :8005) + frontend (npm run dev :3000),显示实时日志,
等两个服务 ready 后自动打开浏览器。关闭窗口时干净杀掉所有子进程。

PyInstaller 打包:
    cd backend
    .venv/Scripts/pyinstaller.exe --onefile --windowed --name OPCLauncher ^
        --distpath ../dist --workpath ../build_pyinstaller ../launcher.py
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import scrolledtext, ttk


# -------------------- 路径与配置 --------------------

def get_opc_root() -> Path:
    """定位 OPC 仓库根目录。

    - exe 模式:exe 位于 ``<opc>/dist/OPCLauncher.exe``,
      向上两级才是仓库根(里面有 backend/、frontend/)。
    - 脚本模式:launcher.py 直接放在 OPC 根,向上零级。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent.parent
    return Path(__file__).parent.resolve()


OPC_ROOT = get_opc_root()
BACKEND_DIR = OPC_ROOT / "backend"
FRONTEND_DIR = OPC_ROOT / "frontend"
BACKEND_PORT = int(os.environ.get("OPC_BACKEND_PORT", "8005"))
FRONTEND_PORT = int(os.environ.get("OPC_FRONTEND_PORT", "3000"))
APP_URL = f"http://localhost:{FRONTEND_PORT}"
HEALTH_TIMEOUT = int(os.environ.get("OPC_STARTUP_TIMEOUT", "60"))
CREATE_NO_WINDOW = 0x08000000  # Windows only — 防止 subprocess 弹黑窗


# -------------------- 主程序 --------------------

class OPCLauncher:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.backend_proc: subprocess.Popen | None = None
        self.frontend_proc: subprocess.Popen | None = None
        self.log_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.backend_ready = False
        self.frontend_ready = False
        self._shutting_down = False

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._shutdown)
        self.root.after(100, self._poll_log_queue)
        threading.Thread(target=self._start_services, daemon=True).start()

    # ---- UI ----

    def _build_ui(self) -> None:
        self.root.title("OPC Launcher")
        self.root.geometry("900x620")
        self.root.minsize(720, 480)

        # 顶部状态
        status_frame = ttk.Frame(self.root, padding=8)
        status_frame.pack(fill="x")
        ttk.Label(status_frame, text="OPC 启动器", font=("Segoe UI", 14, "bold")).pack(side="left")
        self.status_var = tk.StringVar(value="⏳ 正在准备启动...")
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var, foreground="#666")
        self.status_label.pack(side="right")

        # 两个日志面板
        paned = ttk.PanedWindow(self.root, orient="vertical")
        paned.pack(fill="both", expand=True, padx=8, pady=4)

        self.backend_log = self._make_log_panel(paned, f"Backend (port {BACKEND_PORT})")
        self.frontend_log = self._make_log_panel(paned, f"Frontend (port {FRONTEND_PORT})")

        # 底部按钮
        button_frame = ttk.Frame(self.root, padding=8)
        button_frame.pack(fill="x")
        ttk.Button(button_frame, text="🌐 打开浏览器", command=self._open_browser).pack(side="left", padx=4)
        ttk.Button(button_frame, text="🔄 重启服务", command=self._restart_services).pack(side="left", padx=4)
        ttk.Button(button_frame, text="⏹ 关闭", command=self._shutdown).pack(side="right", padx=4)

        # 启动后端/前端日志"开始"
        self._append_log("backend", f"$ cd {BACKEND_DIR}\n$ .venv/Scripts/uvicorn.exe app.main:app --port {BACKEND_PORT}\n")
        self._append_log("frontend", f"$ cd {FRONTEND_DIR}\n$ npm run dev\n")

    def _make_log_panel(self, parent: ttk.PanedWindow, title: str) -> scrolledtext.ScrolledText:
        frame = ttk.Frame(parent)
        parent.add(frame, weight=1)
        ttk.Label(frame, text=title, font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=4, pady=(4, 0))
        text = scrolledtext.ScrolledText(
            frame, wrap="none", height=12,
            font=("Consolas", 9), bg="#0e0e0e", fg="#e0e0e0",
            insertbackground="#e0e0e0",
        )
        text.pack(fill="both", expand=True, padx=4, pady=4)
        text.configure(state="disabled")
        return text

    def _set_status(self, msg: str, color: str = "#666") -> None:
        self.status_var.set(msg)
        self.status_label.configure(foreground=color)

    def _append_log(self, tag: str, line: str) -> None:
        widget = self.backend_log if tag == "backend" else self.frontend_log
        widget.configure(state="normal")
        widget.insert("end", line)
        widget.see("end")
        widget.configure(state="disabled")

    # ---- 子进程 ----

    def _start_services(self) -> None:
        # backend
        try:
            self.backend_proc = subprocess.Popen(
                [
                    str(BACKEND_DIR / ".venv" / "Scripts" / "uvicorn.exe"),
                    "app.main:app", "--host", "0.0.0.0", "--port", str(BACKEND_PORT),
                ],
                cwd=str(BACKEND_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
            )
            threading.Thread(target=self._pipe_output, args=("backend", self.backend_proc.stdout), daemon=True).start()
        except Exception as e:
            self._append_log("backend", f"启动失败: {e}\n")
            self._set_status(f"❌ backend 启动失败: {e}", "#c00")
            return

        # frontend
        try:
            self.frontend_proc = subprocess.Popen(
                ["cmd", "/c", "npm", "run", "dev"],
                cwd=str(FRONTEND_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
            )
            threading.Thread(target=self._pipe_output, args=("frontend", self.frontend_proc.stdout), daemon=True).start()
        except Exception as e:
            self._append_log("frontend", f"启动失败: {e}\n")
            self._set_status(f"❌ frontend 启动失败: {e}", "#c00")
            return

        # 健康检查
        self.root.after(0, lambda: self._set_status("⏳ 等待 backend 就绪..."))

        if self._wait_for(f"http://localhost:{BACKEND_PORT}/openapi.json", HEALTH_TIMEOUT):
            self.backend_ready = True
            self.root.after(0, lambda: self._set_status("⏳ 等待 frontend 就绪..."))
            if self._wait_for(APP_URL, HEALTH_TIMEOUT):
                self.frontend_ready = True
                self.root.after(0, lambda: self._set_status("✅ 就绪 — 打开浏览器中...", "#080"))
                self.root.after(0, self._open_browser)
                return

        # 失败提示
        if not self.backend_ready:
            self.root.after(0, lambda: self._set_status(f"❌ backend {HEALTH_TIMEOUT}s 内未就绪 — 查看日志", "#c00"))
        else:
            self.root.after(0, lambda: self._set_status(f"❌ frontend {HEALTH_TIMEOUT}s 内未就绪 — 查看日志", "#c00"))

    def _pipe_output(self, tag: str, stream) -> None:
        try:
            for raw in iter(stream.readline, b""):
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace")
                self.log_queue.put((tag, line))
        except Exception:
            pass

    def _wait_for(self, url: str, timeout: int) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=1) as resp:
                    if resp.status < 500:
                        return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    # ---- 按钮动作 ----

    def _open_browser(self) -> None:
        webbrowser.open(APP_URL)

    def _restart_services(self) -> None:
        if self._shutting_down:
            return
        self._set_status("🔄 正在重启服务...", "#666")
        threading.Thread(target=self._kill_and_restart, daemon=True).start()

    def _kill_and_restart(self) -> None:
        self._kill_proc(self.backend_proc)
        self._kill_proc(self.frontend_proc)
        self.backend_proc = None
        self.frontend_proc = None
        self.backend_ready = False
        self.frontend_ready = False
        # 清空日志
        for w in (self.backend_log, self.frontend_log):
            w.configure(state="normal")
            w.delete("1.0", "end")
            w.configure(state="disabled")
        self._append_log("backend", f"$ (restart) cd {BACKEND_DIR}\n")
        self._append_log("frontend", f"$ (restart) cd {FRONTEND_DIR}\n")
        self._start_services()

    def _shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._set_status("⏹ 正在关闭...", "#666")
        self._kill_proc(self.backend_proc)
        self._kill_proc(self.frontend_proc)
        self.root.after(200, self.root.destroy)

    @staticmethod
    def _kill_proc(proc: subprocess.Popen | None) -> None:
        if proc is None:
            return
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        except Exception:
            pass

    # ---- log 轮询 ----

    def _poll_log_queue(self) -> None:
        try:
            while True:
                tag, line = self.log_queue.get_nowait()
                self._append_log(tag, line)
        except queue.Empty:
            pass
        if not self._shutting_down:
            self.root.after(100, self._poll_log_queue)


# -------------------- 入口 --------------------

def main() -> None:
    if not (BACKEND_DIR / ".venv" / "Scripts" / "uvicorn.exe").exists():
        # 在 GUI 启动前提示用户,避免弹窗后才发现路径错
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            f"找不到 backend/.venv/Scripts/uvicorn.exe\n\n请确认 OPCLauncher.exe 放在 OPC 仓库根目录的 dist/ 子目录里:\n{OPC_ROOT}",
            "OPC Launcher — 路径错误",
            0x10,
        )
        sys.exit(1)
    root = tk.Tk()
    OPCLauncher(root)
    root.mainloop()


if __name__ == "__main__":
    main()
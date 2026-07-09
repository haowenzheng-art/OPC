"""Agent Tool Registry - 给 LLM 提供 read/list/edit/bash 工具调用能力.

设计目标:
- Stage 1: 工具定义 + 执行器就绪,通过 _should_use_tools() 钩子控制是否启用 (默认关闭)
- Stage 2: 把 _should_use_tools() 改成 True,让 self-repair loop 自动调工具改代码
- Stage 3+: 视觉修复、模板填充都用同一套工具

工具 JSON Schema 用 Anthropic 协议 (name/description/input_schema),因为:
- LLM provider 是 anthropic/minimax (Anthropic Messages API)
- test_agent 已经在用此协议
- 跟现有 llm.create_message(tools=...) 签名天然兼容

安全约束:
- 所有文件操作路径必须落在 project_root 内 (防 LLM 误读 /etc/passwd)
- bash 命令 60s timeout (防 tsc 死循环)
- bash 输出截断到 8000 chars (防 LLM context 爆掉)
- 不允许 rm -rf / 或类似破坏性命令 (basic blacklist)
"""
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable

from app.core.logging import get_logger

log = get_logger(__name__)


# ----------------------------- 工具结果 -----------------------------

@dataclass
class ToolResult:
    """工具执行结果,统一格式喂回 LLM."""
    success: bool
    output: str
    error: str | None = None

    def to_anthropic_tool_result(self, tool_use_id: str) -> dict[str, Any]:
        """转 Anthropic tool_result block 格式."""
        content = self.output if self.success else f"ERROR: {self.error}\n{self.output}"
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content[:8000],  # 截断防 context 爆掉
            "is_error": not self.success,
        }


# ----------------------------- 工具定义 -----------------------------

@dataclass
class ToolDef:
    """工具定义 = JSON Schema + 执行函数."""
    name: str
    description: str
    input_schema: dict[str, Any]
    executor: Callable[..., Awaitable[ToolResult]]


def _resolve_safe_path(project_root: Path, requested: str) -> Path:
    """把请求路径解析到 project_root 内,防止越界访问.

    只允许相对路径;去掉前导 ./ 和正斜杠/反斜杠,但保留 .. 让 relative_to 检查。
    """
    if not requested:
        raise ValueError("path is empty")
    # 拒绝绝对路径 (Windows: C:\... 或 /home/...)
    if requested[0] in ("/", "\\") or (len(requested) >= 2 and requested[1] == ":"):
        raise ValueError(f"absolute path '{requested}' is not allowed")
    # 只去掉前导 ./ (单次),不递归去 ..
    clean = requested
    while clean.startswith(("./", ".\\", "/", "\\")):
        clean = clean[2:] if clean[0] == "." else clean[1:]
    full = (project_root / clean).resolve()
    root_resolved = project_root.resolve()
    try:
        full.relative_to(root_resolved)
    except ValueError:
        raise ValueError(f"path '{requested}' is outside project root {project_root}")
    return full


async def _tool_read_file(project_root: Path, path: str) -> ToolResult:
    try:
        full = _resolve_safe_path(project_root, path)
        if not full.exists():
            return ToolResult(False, "", f"file not found: {path}")
        if not full.is_file():
            return ToolResult(False, "", f"not a file: {path}")
        text = full.read_text(encoding="utf-8", errors="replace")
        return ToolResult(True, text)
    except Exception as e:
        return ToolResult(False, "", f"{type(e).__name__}: {e}")


async def _tool_list_files(project_root: Path, directory: str = ".") -> ToolResult:
    try:
        base = _resolve_safe_path(project_root, directory)
        if not base.exists():
            return ToolResult(False, "", f"directory not found: {directory}")
        if not base.is_dir():
            return ToolResult(False, "", f"not a directory: {directory}")
        # 限制深度为 3,跳过 node_modules 等
        lines: list[str] = []
        for p in sorted(base.rglob("*")):
            try:
                rel = p.relative_to(base)
                # 跳过 node_modules 和 .next
                parts = rel.parts
                if any(part in ("node_modules", ".next", ".git", "dist") for part in parts):
                    continue
                if len(parts) > 4:
                    continue
                kind = "d" if p.is_dir() else "f"
                size = p.stat().st_size if p.is_file() else 0
                lines.append(f"{kind} {size:>8} {str(rel).replace(chr(92), '/')}")
            except (OSError, ValueError):
                continue
        if not lines:
            return ToolResult(True, "(empty directory)")
        return ToolResult(True, "\n".join(lines[:200]))  # 最多 200 行
    except Exception as e:
        return ToolResult(False, "", f"{type(e).__name__}: {e}")


async def _tool_edit_file(project_root: Path, path: str, old_text: str, new_text: str) -> ToolResult:
    try:
        full = _resolve_safe_path(project_root, path)
        if not full.exists():
            return ToolResult(False, "", f"file not found: {path}")
        text = full.read_text(encoding="utf-8", errors="replace")
        count = text.count(old_text)
        if count == 0:
            return ToolResult(False, "", f"old_text not found in {path}. 请先用 read_file 确认当前内容。")
        if count > 1:
            return ToolResult(False, "", f"old_text 在 {path} 中出现 {count} 次,无法确定改哪一处。请提供更精确的 old_text (含上下文行)。")
        new_content = text.replace(old_text, new_text, 1)
        full.write_text(new_content, encoding="utf-8")
        return ToolResult(True, f"成功修改 {path} ({len(old_text)} → {len(new_text)} chars)")
    except Exception as e:
        return ToolResult(False, "", f"{type(e).__name__}: {e}")


# bash 黑名单:防止破坏性操作
_BASH_DENY_PATTERNS = [
    r"\brm\s+-rf\s+[/\\]",          # rm -rf /
    r"\bformat\s+[a-zA-Z]:",         # format C:
    r"\bdel\s+/[sq]",                # del /s /q
    r"\bRemove-Item\b.*-Recurse.*-Force",
    r":\(\)\s*\{.*\};:",             # fork bomb
    r"\bdd\s+if=.*of=/dev/",         # dd to device
    r"\bshutdown\b",
    r"\bregistry::",                 # Windows registry hack
    r"\bInvoke-WebRequest.*-OutFile.*\.exe",  # download+run exe
]


def _bash_is_safe(command: str) -> tuple[bool, str]:
    for pat in _BASH_DENY_PATTERNS:
        if re.search(pat, command, re.IGNORECASE):
            return False, f"命令包含被禁止的模式: {pat}"
    return True, ""


async def _tool_bash(project_root: Path, command: str, timeout: int = 60) -> ToolResult:
    safe, reason = _bash_is_safe(command)
    if not safe:
        return ToolResult(False, "", reason)
    try:
        # Windows 下需要 shell=True 来解析 .cmd/.ps1;但同时有命令注入风险。
        # 这里用 list 模式 + shell=False 通过 cmd.exe 调度 (Node .cmd 也能找到)。
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(project_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ToolResult(False, "", f"命令超时 ({timeout}s),已 kill")
        output = stdout.decode("utf-8", errors="replace")
        truncated = output[-8000:] if len(output) > 8000 else output
        if proc.returncode != 0:
            return ToolResult(False, truncated, f"exit code {proc.returncode}")
        return ToolResult(True, truncated or "(no output)")
    except Exception as e:
        return ToolResult(False, "", f"{type(e).__name__}: {e}")


async def _tool_apply_patch(project_root: Path, patch: str) -> ToolResult:
    """简化版 unified diff 应用:解析 +++ b/path --- a/path @@ ... @@ 块。

    只支持 add/modify (新增 / 修改文件),不支持 delete。
    返回每个文件的成功/失败汇总。
    """
    try:
        # 找文件块: "+++ b/path/to/file"
        file_pattern = re.compile(r"\+\+\+\s+([^\s]+)")
        # 找 hunk header: "@@ -X,Y +A,B @@"
        hunk_pattern = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@", re.MULTILINE)
        results: list[str] = []
        # split by file
        chunks = re.split(r"^diff --git\s+", patch, flags=re.MULTILINE)
        for chunk in chunks:
            if not chunk.strip():
                continue
            m = file_pattern.search(chunk)
            if not m:
                continue
            target = m.group(1).removeprefix("b/")
            try:
                full = _resolve_safe_path(project_root, target)
            except ValueError as ve:
                results.append(f"SKIP {target}: {ve}")
                continue
            full.parent.mkdir(parents=True, exist_ok=True)
            # 解析 hunks 并应用到 existing 内容
            existing = full.read_text(encoding="utf-8") if full.exists() else ""
            existing_lines = existing.splitlines(keepends=True)
            new_lines: list[str] = []
            cur_old = 0
            for hunk_m in hunk_pattern.finditer(chunk):
                old_start = int(hunk_m.group(1))
                old_count = int(hunk_m.group(2) or "1")
                new_start = int(hunk_m.group(3))
                new_count = int(hunk_m.group(4) or "1")
                # 找 hunk body
                body_start = hunk_m.end()
                next_hunk = hunk_pattern.search(chunk, body_start)
                body_end = next_hunk.start() if next_hunk else len(chunk)
                body = chunk[body_start:body_end]
                # 复制 hunk 之前的 unchanged 行
                if old_start - 1 > cur_old:
                    new_lines.extend(existing_lines[cur_old:old_start - 1])
                # 应用 hunk
                for line in body.splitlines(keepends=True):
                    if line.startswith("+"):
                        new_lines.append(line[1:])
                    elif line.startswith("-"):
                        pass  # 删除
                    elif line.startswith(" ") or line.strip() == "":
                        # context 或空行
                        if line.startswith(" "):
                            new_lines.append(line[1:])
                        else:
                            new_lines.append("\n" if not new_lines or new_lines[-1].endswith("\n") else "")
                    # else: 注释行 (\),跳过
                cur_old = old_start - 1 + old_count
            # 剩余 unchanged
            if cur_old < len(existing_lines):
                new_lines.extend(existing_lines[cur_old:])
            full.write_text("".join(new_lines), encoding="utf-8")
            results.append(f"OK {target} ({len(existing)} → {len(''.join(new_lines))} chars)")
        if not results:
            return ToolResult(False, "", "no valid file blocks found in patch")
        return ToolResult(True, "\n".join(results))
    except Exception as e:
        return ToolResult(False, "", f"{type(e).__name__}: {e}")


# ----------------------------- 工具注册表 -----------------------------

def _build_default_tools(project_root: Path) -> list[ToolDef]:
    return [
        ToolDef(
            name="read_file",
            description="读取项目内任意文件的内容。返回 utf-8 文本。用于查看自己刚生成的代码、查 import 关系、确认修复前的现状。",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对项目根的路径,如 'frontend/src/app/page.tsx'"},
                },
                "required": ["path"],
            },
            executor=lambda **kw: _tool_read_file(project_root, kw["path"]),
        ),
        ToolDef(
            name="list_files",
            description="列出目录内的文件和子目录 (深度 ≤ 3)。自动跳过 node_modules / .next / dist / .git。用于了解项目结构。",
            input_schema={
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "相对项目根的目录,默认 '.' 即整个项目", "default": "."},
                },
                "required": [],
            },
            executor=lambda **kw: _tool_list_files(project_root, kw.get("directory", ".")),
        ),
        ToolDef(
            name="edit_file",
            description="精确字符串替换:在 path 文件里把 old_text 替换为 new_text。old_text 必须唯一匹配;若不唯一或找不到,请先 read_file 确认。",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径 (相对项目根)"},
                    "old_text": {"type": "string", "description": "要被替换的原文,必须恰好出现 1 次"},
                    "new_text": {"type": "string", "description": "新内容"},
                },
                "required": ["path", "old_text", "new_text"],
            },
            executor=lambda **kw: _tool_edit_file(project_root, kw["path"], kw["old_text"], kw["new_text"]),
        ),
        ToolDef(
            name="bash",
            description="在项目根目录执行 shell 命令 (60s timeout)。常用于: tsc --noEmit、npm install、curl localhost:3001/api/v1/health、cat package.json。命令会被截断到 8000 chars。",
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "shell 命令字符串,如 'npx tsc --noEmit' 或 'curl -s http://localhost:3001/health'"},
                    "timeout": {"type": "integer", "description": "超时秒数,默认 60", "default": 60},
                },
                "required": ["command"],
            },
            executor=lambda **kw: _tool_bash(project_root, kw["command"], kw.get("timeout", 60)),
        ),
        ToolDef(
            name="apply_patch",
            description="应用 unified diff 格式的 patch。用于一次性改多个文件或做大段替换。每个 hunk 单独应用,失败不影响其他 hunk。",
            input_schema={
                "type": "object",
                "properties": {
                    "patch": {"type": "string", "description": "unified diff 内容,格式: 'diff --git a/path\\n--- a/path\\n+++ b/path\\n@@ -X,Y +A,B @@\\n context\\n-old\\n+new'"},
                },
                "required": ["patch"],
            },
            executor=lambda **kw: _tool_apply_patch(project_root, kw["patch"]),
        ),
    ]


class ToolRegistry:
    """工具注册表:绑定到具体 project_root,提供 anthropic 格式的 tool definitions + 执行。"""

    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self._tools: dict[str, ToolDef] = {t.name: t for t in _build_default_tools(self.project_root)}

    def get_anthropic_tools(self) -> list[dict[str, Any]]:
        """返回 Anthropic Messages API tools 参数格式."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in self._tools.values()
        ]

    async def execute(self, name: str, tool_input: dict[str, Any]) -> ToolResult:
        if name not in self._tools:
            return ToolResult(False, "", f"unknown tool: {name}")
        tool = self._tools[name]
        try:
            return await tool.executor(**tool_input)
        except Exception as e:
            return ToolResult(False, "", f"executor crashed: {type(e).__name__}: {e}")

    def list_tool_names(self) -> list[str]:
        return list(self._tools.keys())
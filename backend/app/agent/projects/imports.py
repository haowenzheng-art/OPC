"""TS/JS import 扫描 + package.json 依赖注入工具.

抽出到独立模块避免 backend_agent ↔ verify.py 循环 import。
backend_agent 和 frontend_agent 都用这套扫描 LLM 生成的代码,
把模板 package.json 没声明的第三方包自动注入。
"""
from __future__ import annotations

import json
import re

from app.core.logging import get_logger

log = get_logger(__name__)


# Node 内置模块白名单 — 这些不需要进 package.json
NODE_BUILTINS = {
    "assert", "async_hooks", "buffer", "child_process", "cluster", "console",
    "constants", "crypto", "dgram", "dns", "domain", "events", "fs", "http",
    "http2", "https", "inspector", "module", "net", "os", "path", "perf_hooks",
    "process", "punycode", "querystring", "readline", "repl", "stream",
    "string_decoder", "sys", "timers", "tls", "trace_events", "tty", "url",
    "util", "v8", "vm", "wasi", "worker_threads", "zlib",
}


def scan_third_party_imports(code: str) -> set[str]:
    """从 TS/JS 代码中扫描第三方包 import,返回需要 npm install 的包名集合.

    例: `import { format } from 'date-fns'` → {'date-fns'}
        `import { toZonedTime } from 'date-fns-tz'` → {'date-fns-tz'}
        `import express from 'express'` → {'express'}
        `import './routes.js'` → {} (相对路径)
        `import 'fs'` → {} (Node 内置)
    """
    deps: set[str] = set()
    patterns = [
        re.compile(r"""(?:import|export)[^'"]*?from\s*['"]([^'"]+)['"]"""),
        re.compile(r"""import\s*\(\s*['"]([^'"]+)['"]\s*\)"""),
    ]
    for pattern in patterns:
        for m in pattern.finditer(code):
            spec = m.group(1).strip()
            if spec.startswith(".") or spec.startswith("/") or spec.startswith("node:"):
                continue
            parts = spec.split("/")
            if spec.startswith("@"):
                pkg = "/".join(parts[:2])
            else:
                pkg = parts[0]
            if pkg in NODE_BUILTINS:
                continue
            deps.add(pkg)
    return deps


def merge_deps_into_package_json(package_json: str, extra_deps: set[str]) -> str:
    """把额外依赖合并进 package.json 的 dependencies (用 * 版本让 npm 解析最新)."""
    if not extra_deps:
        return package_json
    pkg = json.loads(package_json)
    deps = pkg.setdefault("dependencies", {})
    for dep in extra_deps:
        if dep not in deps:
            deps[dep] = "*"
            log.info("agent_inject_dep", dep=dep)
    return json.dumps(pkg, indent=2) + "\n"

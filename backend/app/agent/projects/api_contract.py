"""API 契约 — 机器可读,替换 markdown api_spec.

核心思路:
- Backend Agent 生成 routes.ts 后,**静态分析**提取所有 router.METHOD('/path') 调用
- mount_prefix 从 index.ts 的 app.use(...) 解析,不硬编码
- 输出 api_contract.json,frontend agent 消费
- frontend 生成 page.tsx 后,reconcile_fetch_urls 机械修正任何不在契约里的 fetch URL

这样三个来源(spec markdown / index.ts mount / routes.ts paths)被收敛到
"routes.ts 是唯一真相来源,contract 从它派生",消除 LLM 幻觉空间。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass


# 匹配 router.get('/path', ...), router.post("/path", ...), 跨行也行
# 支持 router.METHOD(`/path/${var}`) 模板字符串
_ROUTE_PATTERN = re.compile(
    r"""router\.(get|post|put|delete|patch)\(\s*['"`]([^'"`]+)['"`]""",
    re.DOTALL,
)

# 匹配 app.use('/api/v1', routes) — 提取 mount prefix
_MOUNT_PATTERN = re.compile(
    r"""app\.use\(\s*['"`]([^'"`]+)['"`]\s*,\s*routes""",
)


def derive_mount_prefix(index_ts: str) -> str:
    """从 index.ts 解析 app.use('/api/v1', routes) 的 mount prefix.

    找不到则返回空串(表示 routes 挂在根路径)。
    """
    m = _MOUNT_PATTERN.search(index_ts)
    return m.group(1) if m else ""


def derive_api_contract(routes_ts: str, mount_prefix: str) -> dict | None:
    """静态分析 routes.ts,提取所有 router.METHOD('/path') 调用.

    返回 None = 解析失败(应视为 backend defect 触发重试)。
    返回 dict = 契约 JSON,包含 mount_prefix + endpoints[]。

    endpoints[] 每项:
      {"method": "GET", "path": "/todos", "full": "/api/v1/todos"}
      full = mount_prefix + path,供 frontend 直接拼 URL 用
    """
    endpoints: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for m in _ROUTE_PATTERN.finditer(routes_ts):
        method = m.group(1).upper()
        path = m.group(2)
        # 去重 (router.get('/todos') 出现两次只算一次)
        key = (method, path)
        if key in seen:
            continue
        seen.add(key)
        full = (mount_prefix.rstrip("/") + "/" + path.lstrip("/")) if mount_prefix else path
        endpoints.append({"method": method, "path": path, "full": full})

    if not endpoints:
        return None

    return {
        "mount_prefix": mount_prefix,
        "endpoints": endpoints,
        "derived_from": "routes.ts",
        "version": 1,
    }


# 匹配 fetch(`${API_BASE}/path`) 或 fetch(`${API}/api/v1/todos`) 或 fetch(`${BASE}/todos`)
# 目标:抓 fetch 的 URL 参数里的路径部分
_FETCH_URL_PATTERNS = [
    # fetch(`${VAR}/api/v1/...`) — 模板字符串带 /api/v1 前缀
    re.compile(r"""fetch\(\s*`([^`]*?)(/api/v[^`]+)`\s*"""),
    # fetch(`${VAR}/relative/path`) — 模板字符串,相对路径不带 /api 前缀 (如 /todos, /items)
    re.compile(r"""fetch\(\s*`([^`]*?)(/[^`\s'"):]+)`\s*"""),
    # fetch('http://localhost:3001/api/v1/...') 或 fetch("http://localhost:3001/api/v1/...")
    re.compile(r"""fetch\(\s*['"]([^'"]*?)(/api/[^'"]+)['"]\s*"""),
    # fetch('/api/v1/todos') 或 fetch("/todos") — 绝对路径字符串 (prefix 为空)
    re.compile(r"""fetch\(\s*['"]([/][^'"]+)['"]\s*\)"""),
]


# 过滤掉 API mount prefix 本身 (如 /api/v1/) — 它不是有效 endpoint
_API_MOUNT_RE = re.compile(r"^/api/v\d+/?$")


def _extract_fetch_urls(page_tsx: str) -> list[tuple[str, str]]:
    """提取 page.tsx 中所有 fetch() 的 URL,返回 [(prefix, path), ...].

    prefix 可能是 ${API_BASE} / ${API} / http://localhost:3001 / "" 等
    path 是 /api/v1/todos 这部分
    """
    results: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pattern in _FETCH_URL_PATTERNS:
        for m in pattern.finditer(page_tsx):
            # 统一: group 1 = prefix, group 2 = path
            if m.lastindex == 1:
                # 单组 pattern (如 pattern 4: fetch('/path')) — prefix 为空
                prefix, path = "", m.group(1)
            else:
                prefix, path = m.group(1), m.group(2)
            # 过滤: API mount prefix 本身 (如 /api/v1/) 不是有效 endpoint
            if _API_MOUNT_RE.match(path):
                continue
            key = (prefix, path)
            if key not in seen:
                seen.add(key)
                results.append((prefix, path))
    return results


def _normalize_path_for_match(path: str) -> str:
    """归一化路径用于契约匹配.

    /todos/123 → /todos/:param (但 contract 里存的是 /todos/:id)
    /todos → /todos
    /todos?limit=10 → /todos
    /todos/${id} → /todos/:id (模板语法归一化)
    /todos/${todo.id} → /todos/:id (属性访问模板归一化)
    """
    # 去掉 query string
    path = path.split("?")[0]
    # 去掉末尾斜杠
    path = path.rstrip("/") if len(path) > 1 else path
    # 模板语法归一化: ${id} → :id (匹配 contract 的 :param 格式)
    # 也处理属性访问: ${todo.id} / ${item.id} / ${entry.name} → :id
    path = re.sub(r"\$\{[^.]+\.([^}]+)\}", r":\1", path)  # ${obj.id} → :id
    path = re.sub(r"\$\{([^}]+)\}", r":\1", path)  # ${id} → :id
    return path


def _path_matches_contract(fetch_path: str, contract: dict) -> str | None:
    """检查 fetch 路径是否匹配契约中某个 endpoint.

    返回匹配的 full path,或 None 表示不匹配。
    支持 /todos/123 匹配契约里的 /todos/:id (路径参数)。
    """
    norm = _normalize_path_for_match(fetch_path)
    endpoints = contract.get("endpoints", [])
    mount_prefix = contract.get("mount_prefix", "")

    # 直接匹配
    for ep in endpoints:
        if norm == ep["full"] or norm == ep["path"]:
            return ep["full"]

    # 路径参数匹配:/todos/:id 能匹配 /todos/123
    for ep in endpoints:
        ep_path = ep["full"] if mount_prefix and ep["full"].startswith(mount_prefix) else ep["path"]
        # 把 :id 转成捕获组
        pattern = re.sub(r":[^/]+", r"[^/]+", re.escape(ep_path).replace(r"\:", ":"))
        # re.escape 把 : 转义了,需要还原
        pattern = pattern.replace(r"\:", ":").replace(":[^/]+", r":[^/]+").replace(r":", r":")
        # 重新构建:把 escape 后的 :id 还原成 [^/]+
        pattern = re.escape(ep_path).replace(r"\:", ":")
        pattern = re.sub(r":[^/]+", r"[^/]+", pattern)
        if re.fullmatch(pattern, norm):
            return ep["full"]

    return None


def reconcile_fetch_urls(page_tsx: str, contract: dict) -> tuple[str, list[str]]:
    """机械修正 page.tsx 中不在契约里的 fetch URL.

    返回 (改写后的 page_tsx, 被改写的 URL 列表)。
    若 URL 不匹配任何 endpoint,改写为契约里第一个 GET endpoint (兜底)。
    若契约本身没有 endpoint,不做任何改写(返回原文)。
    """
    endpoints = contract.get("endpoints", [])
    if not endpoints:
        return page_tsx, []

    # 找一个默认 GET endpoint 兜底
    default_ep = next((e for e in endpoints if e["method"] == "GET"), endpoints[0])
    default_path = default_ep["full"]

    rewritten: list[str] = []
    result = page_tsx

    for pattern in _FETCH_URL_PATTERNS:
        def _make_replace(default_path: str, contract: dict, rewritten: list):
            def _replace(m: re.Match) -> str:
                # 统一: group 1 = prefix (可能为空), group 2 = path
                if m.lastindex == 1:
                    prefix, path = "", m.group(1)
                else:
                    prefix, path = m.group(1), m.group(2)
                # 过滤: API mount prefix 本身 (如 /api/v1/) 不是有效 endpoint
                if _API_MOUNT_RE.match(path):
                    return m.group(0)  # 不匹配但也跳过,不对 API prefix 改写
                matched = _path_matches_contract(path, contract)
                if matched is not None:
                    return m.group(0)  # 已匹配,不改
                # 不匹配,改写为 default
                rewritten.append(path)
                # 重建 fetch 调用,保持引号风格
                full_match = m.group(0)
                if "`" in full_match:
                    return f"fetch(`{prefix}{default_path}`"
                quote = "'" if "'" in full_match else '"'
                return f"fetch({quote}{prefix}{default_path}{quote}"
            return _replace

        result = pattern.sub(_make_replace(default_path, contract, rewritten), result)

    return result, rewritten

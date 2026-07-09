"""API 契约预测 — 从 PRD 提前推导出 API 端点, 用于 Backend/Frontend 并行.

传统流程 (串行):
  Backend 60s → 生成 routes.ts → derive_api_contract → Frontend 60s
  总耗时 120s

并行流程:
  predict_api_contract(PRD) 3s  ─┬─→ Backend 60s
                                  └─→ Frontend 60s (用 predicted contract)
  total: max(60, 60) + 3 = ~63s
  节省 ~57s/项目

预测 contract 与 Backend 实际 contract 可能不一致, 解决:
- Frontend 用 predicted contract 生成 page.tsx
- Backend 完成后, 用 actual contract 做 reconcile_fetch_urls 改写 fetch URL
- 如果差异大, Frontend 重试 (走现有 retry 机制)
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.agent.llm import LLMClient
from app.agent.projects.utils import llm_chat
from app.core.logging import get_logger

log = get_logger(__name__)


_PREDICTION_SYSTEM_PROMPT = """你是一位 API 设计师. 根据 PRD (产品需求文档) 预测这个项目需要的所有 API 端点.

输出严格 JSON 格式:
{
  "endpoints": [
    {"method": "GET", "path": "/todos", "description": "列出所有待办"},
    {"method": "POST", "path": "/todos", "description": "创建待办"},
    {"method": "PATCH", "path": "/todos/:id", "description": "更新待办"},
    {"method": "DELETE", "path": "/todos/:id", "description": "删除待办"}
  ]
}

规则:
- 至少包含基础 CRUD: GET list, GET :id, POST create, PATCH :id, DELETE :id
- path 用 :id 表示动态参数 (如 :todoId, :userId)
- method 必须是 GET/POST/PUT/PATCH/DELETE 大写
- description 一句话中文说明
- 简单项目 (todo, note, calculator) 保持精简, 5-8 个端点足够
- 只输出 JSON, 不要解释"""


async def predict_api_contract(
    prd: str,
    mount_prefix: str = "/api/v1",
    llm: LLMClient | None = None,
) -> dict | None:
    """从 PRD 预测 API 契约, 返回与 derive_api_contract 相同 schema 的 dict.

    返回 None = 预测失败, 调用方应回退到串行模式.
    """
    if not prd or not prd.strip():
        log.warning("predict_api_contract_empty_prd")
        return None

    try:
        raw = await llm_chat(
            system=_PREDICTION_SYSTEM_PROMPT,
            user=f"PRD:\n{prd}\n\n请预测 API 端点.",
            temperature=0.2,
            max_tokens=2000,
            llm=llm,
        )
    except Exception as e:
        log.warning("predict_api_contract_llm_failed", error=str(e))
        return None

    endpoints = _parse_endpoints(raw)
    if not endpoints:
        log.warning("predict_api_contract_parse_failed", raw_len=len(raw))
        return None

    # 与 derive_api_contract 同 schema: 每条 endpoint 带 full = mount_prefix + path
    # LLM 可能输出 "/api/v1/todos" 形式的 path (含 mount prefix),先剥掉再加
    normalized_mount = mount_prefix.rstrip("/")
    for ep in endpoints:
        p = ep["path"]
        # 如果 path 已经包含 mount prefix,剥掉以便与 derive_api_contract 一致
        if normalized_mount and p.startswith(normalized_mount + "/"):
            ep["path"] = p[len(normalized_mount):]
            p = ep["path"]
        ep["full"] = (normalized_mount + p) if normalized_mount else p

    return {
        "mount_prefix": mount_prefix,
        "endpoints": endpoints,
        "derived_from": "prd_predicted",
        "version": 1,
    }


def _parse_endpoints(raw: str) -> list[dict] | None:
    """从 LLM 输出解析 endpoints 列表."""
    text = raw.strip()
    # 去掉 markdown code fence
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    # 尝试整体解析
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "endpoints" in data:
            return _normalize_endpoints(data["endpoints"])
        if isinstance(data, list):
            return _normalize_endpoints(data)
    except json.JSONDecodeError:
        pass

    # 容错: 提取 {...} 之间的内容
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict) and "endpoints" in data:
                return _normalize_endpoints(data["endpoints"])
        except json.JSONDecodeError:
            pass

    return None


def _normalize_endpoints(endpoints: list[Any]) -> list[dict] | None:
    """校验和规范化 endpoint 列表."""
    if not isinstance(endpoints, list) or not endpoints:
        return None
    valid: list[dict] = []
    for ep in endpoints:
        if not isinstance(ep, dict):
            continue
        method = str(ep.get("method", "")).upper()
        path = str(ep.get("path", "")).strip()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            continue
        if not path.startswith("/"):
            continue
        valid.append({"method": method, "path": path})
    # 去重 (按 method+path)
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for ep in valid:
        key = (ep["method"], ep["path"])
        if key not in seen:
            seen.add(key)
            deduped.append(ep)
    if not deduped:
        return None
    return deduped


def get_default_crud_contract(resource: str = "items", mount_prefix: str = "/api/v1") -> dict:
    """最简 CRUD fallback: 用于预测完全失败时, 至少给个能跑起来的基础结构.

    Frontend 用这个也能生成可工作代码 (后面会被 Backend 实际 contract 覆盖).
    """
    return {
        "mount_prefix": mount_prefix,
        "endpoints": [
            {"method": "GET", "path": f"/{resource}", "full": f"{mount_prefix}/{resource}"},
            {"method": "POST", "path": f"/{resource}", "full": f"{mount_prefix}/{resource}"},
            {"method": "PATCH", "path": f"/{resource}/:id", "full": f"{mount_prefix}/{resource}/:id"},
            {"method": "DELETE", "path": f"/{resource}/:id", "full": f"{mount_prefix}/{resource}/:id"},
        ],
        "derived_from": "default_crud",
        "version": 1,
    }
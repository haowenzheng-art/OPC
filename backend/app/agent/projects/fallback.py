"""两级 fallback — LLM 卡住时兜底,保证用户永远看到能跑的 preview.

Level 1: simplify_prd — PRD 太复杂导致 LLM 卡住时, 砍掉复杂功能重跑 pipeline.
Level 2: load_fallback_files — Level 1 也失败, 直接用预验证的 todo 模板.

模板在 fallback_template/ 目录, 是预验证过的极简 todo app (Express+Next+in-memory),
不依赖外部 DB, npm install 后直接能跑.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agent.projects.utils import llm_chat
from app.core.logging import get_logger

log = get_logger(__name__)


_TEMPLATE_DIR = Path(__file__).parent / "fallback_template"


def load_fallback_files() -> dict[str, str]:
    """加载预验证的 fallback 模板文件.

    返回 dict 形如 {"backend/package.json": "...", "frontend/src/app/page.tsx": "..."},
    与 agent.get_files() 输出格式一致, 可直接物化到 generated-projects 目录.
    """
    files: dict[str, str] = {}
    for sub in ("backend", "frontend"):
        sub_dir = _TEMPLATE_DIR / sub
        if not sub_dir.exists():
            raise FileNotFoundError(f"fallback template missing: {sub_dir}")
        for f in sub_dir.rglob("*"):
            if f.is_file():
                rel = f.relative_to(_TEMPLATE_DIR).as_posix()
                files[rel] = f.read_text(encoding="utf-8")
    log.info("fallback_files_loaded", file_count=len(files))
    return files


async def simplify_prd(prd: str, llm: Any = None) -> str:
    """Level 1 fallback: 让 LLM 把复杂 PRD 简化成单一资源的 CRUD.

    只保留一个核心实体 + 基础 CRUD 端点, 砍掉认证/支付/外部集成等复杂功能.
    如果 LLM 调用失败, 返回一个 hardcoded 的极简 PRD (todo list).
    """
    prompt = f"""你是产品经理. 把下面的 PRD 简化成最简版本, 只保留:
- 一个核心数据实体 (如 Todo, Note, Task)
- 基础 CRUD 操作 (列表/创建/删除)
- 砍掉认证/支付/外部集成/复杂业务逻辑

原始 PRD:
{prd}

只输出简化后的 PRD (中文, 200 字以内), 不要解释."""
    try:
        simplified = await llm_chat(
            system="你是产品经理. 输出简洁的 PRD, 不解释.",
            user=prompt,
            temperature=0.2,
            llm=llm,
        )
        if simplified and len(simplified) > 20:
            log.info("prd_simplified", original_len=len(prd), simplified_len=len(simplified))
            return simplified
    except Exception as e:
        log.warning("simplify_prd_failed", error=str(e))

    # LLM 失败时的 hardcoded fallback
    return "一个简单的待办事项应用: 用户可以创建、查看、删除待办事项, 标记完成状态. 不需要认证, 不需要分类, 不需要优先级."


def get_fallback_contract() -> dict | None:
    """返回 fallback 模板的 api_contract (供 orchestrator 跳过验证用)."""
    import json

    contract_path = _TEMPLATE_DIR / "backend" / "api_contract.json"
    if not contract_path.exists():
        return None
    return json.loads(contract_path.read_text(encoding="utf-8"))

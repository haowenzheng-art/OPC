"""P2-4: Intent Router — 把用户需求分类到合适的模板.

设计:
- 用 LLM 分类 (5 个模板: todo / landing-page / dashboard / form / calculator)
- 命中模板 → 走快路径 (直接用模板文件 + slot filling, 跳过生成)
- 没命中 → 0→1 走通用 self-repair loop (路径 B)

精简版: 这次只做
- 5 个模板元数据定义 (id, name, description, slots, intents)
- IntentRouter.route(user_idea) → 命中模板 id 或 None
- mock LLM 分类 + 单测
- 完整 fast-path 集成 (template_loader 走 fallback.py 已有逻辑) 留后续 PR

为什么先做 router: router 是入口, 模板文件可以直接复用 fallback_template/.
完整 fast-path 改造需要改 orchestrator._run_develop_test_loop, 工作量大.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class TemplateSlot:
    """模板里的可变字段, LLM 从 user_idea 提取填入."""
    name: str           # "title" / "color_scheme" / "fields"
    description: str    # 怎么填 (给 LLM 的 prompt)
    required: bool = True


@dataclass
class TemplateSpec:
    """模板元数据."""
    id: str                            # "todo-app-v2" / "landing-page-v1" / "dashboard-v1" / "form-v1" / "calculator-v1"
    name: str                          # "Todo App v2"
    description: str                   # 一句话描述, 给 LLM 分类用
    intent_examples: list[str]         # 命中这个模板的样例 (e.g. ["做一个 todo", "待办事项", "task list"])
    slots: list[TemplateSlot]          # 模板的可变字段
    template_dir: str                  # 模板文件目录 (相对 app/agent/projects/)
    api_contract: dict | None = None   # 模板的 API contract, 给 frontend 生成时用
    design_spec: dict | None = None    # 模板的 design spec, 视觉评估用


# 5 个核心模板的元数据 — 完整模板文件后续 PR 补
TEMPLATES: list[TemplateSpec] = [
    TemplateSpec(
        id="todo-app-v2",
        name="Todo App v2",
        description="简单的待办事项应用, 支持增删改查 + 完成状态切换",
        intent_examples=[
            "做一个 todo list", "待办事项", "做一个待办", "task list", "做一个 todo",
            "做一个任务清单", "做一个 to-do", "做一个任务列表",
        ],
        slots=[
            TemplateSlot("title", "应用标题 (e.g. '我的待办', '工作清单')", required=False),
            TemplateSlot("color_scheme", "主色调 (blue/green/purple/orange)", required=False),
        ],
        template_dir="templates/todo-app-v2",
    ),
    TemplateSpec(
        id="landing-page-v1",
        name="Landing Page",
        description="营销/产品介绍落地页, 含 hero + 特性 + CTA",
        intent_examples=[
            "做一个落地页", "landing page", "产品介绍页", "营销页",
            "做一个首页", "做一个产品 page", "做一个公司主页",
        ],
        slots=[
            TemplateSlot("product_name", "产品名", required=True),
            TemplateSlot("tagline", "一句话宣传", required=True),
            TemplateSlot("cta_text", "行动号召按钮文字 (e.g. '立即试用')", required=False),
        ],
        template_dir="templates/landing-page-v1",
    ),
    TemplateSpec(
        id="dashboard-v1",
        name="Dashboard",
        description="数据看板, 含卡片网格 + 简单图表 (纯前端, 不依赖后端)",
        intent_examples=[
            "做一个 dashboard", "数据看板", "管理后台", "数据展示页",
            "做一个统计页面", "做一个 admin 页面", "做一个 metrics page",
        ],
        slots=[
            TemplateSlot("title", "看板标题", required=False),
            TemplateSlot("metrics", "要展示的指标 (e.g. '用户数, 收入, 转化率')", required=True),
        ],
        template_dir="templates/dashboard-v1",
    ),
    TemplateSpec(
        id="form-v1",
        name="Form App",
        description="表单应用, 含校验 + 提交状态 + 成功/失败提示",
        intent_examples=[
            "做一个表单", "问卷", "注册表单", "联系表单", "反馈表单",
            "做一个 form", "做一个 survey", "做一个申请表",
        ],
        slots=[
            TemplateSlot("form_purpose", "表单用途 (e.g. '用户注册', '联系咨询')", required=True),
            TemplateSlot("fields", "要收集的字段 (e.g. '姓名, 邮箱, 留言')", required=True),
        ],
        template_dir="templates/form-v1",
    ),
    TemplateSpec(
        id="calculator-v1",
        name="Calculator",
        description="计算器类应用 (汇率 / BMI / 贷款 / 单位换算 等)",
        intent_examples=[
            "做一个计算器", "做一个 BMI", "做一个汇率转换", "做一个贷款计算",
            "做一个换算", "做一个汇率计算", "做个换算器", "calculator",
        ],
        slots=[
            TemplateSlot("calc_type", "计算器类型 (e.g. 'BMI', '汇率', '贷款')", required=True),
            TemplateSlot("formula", "计算公式说明 (e.g. '体重 / 身高^2')", required=False),
        ],
        template_dir="templates/calculator-v1",
    ),
]


# 预计算 template lookup table
_TEMPLATE_BY_ID: dict[str, TemplateSpec] = {t.id: t for t in TEMPLATES}


def get_template(template_id: str) -> TemplateSpec | None:
    return _TEMPLATE_BY_ID.get(template_id)


def list_templates() -> list[TemplateSpec]:
    return list(TEMPLATES)


# --- Intent Router ---

CLASSIFY_PROMPT = """你是产品经理. 下面有 5 个 OPC 模板, 根据用户的 user_idea 判断最匹配哪个.

## 模板列表
{template_list}

## 用户需求
{user_idea}

## 输出
输出最匹配的模板 id (从上面 5 个里选), 或者 "none" (没匹配上).
只输出一行: 模板 id 或 none, 不要解释.

匹配规则:
- 强相关: 用户明确说要 todo/待办/任务清单 → todo-app-v2
- 强相关: 用户要介绍/营销/落地页 → landing-page-v1
- 强相关: 用户要 dashboard/看板/管理后台 → dashboard-v1
- 强相关: 用户要表单/问卷/注册 → form-v1
- 强相关: 用户要计算器/换算/BMI → calculator-v1
- 弱相关或模糊 → none (走 0→1 自由生成)
"""


def _format_template_list() -> str:
    lines = []
    for t in TEMPLATES:
        examples = ", ".join(t.intent_examples[:3])
        lines.append(f"- id: {t.id}\n  描述: {t.description}\n  典型意图: {examples}")
    return "\n".join(lines)


def build_classify_messages(user_idea: str) -> list[dict[str, Any]]:
    """构造发给 LLM 的 messages, 让它分类 user_idea."""
    return [
        {
            "role": "user",
            "content": CLASSIFY_PROMPT.format(
                template_list=_format_template_list(),
                user_idea=user_idea,
            ),
        }
    ]


def parse_classify_response(raw_content: list[dict[str, Any]]) -> str | None:
    """从 LLM 返回里提取模板 id."""
    text = ""
    for block in raw_content:
        if block.get("type") == "text":
            text = block.get("text", "").strip().lower()
            break

    # 第一行 = 模板 id 或 none
    first_line = text.splitlines()[0].strip() if text else ""
    first_line = first_line.replace("`", "").replace("*", "").strip()

    if first_line in ("none", "null", ""):
        return None
    if first_line in _TEMPLATE_BY_ID:
        return first_line
    # 模糊匹配 (LLM 可能返回 "todo-app-v2 " 带空格, 或拼写错误)
    for tid in _TEMPLATE_BY_ID:
        if tid in first_line or first_line in tid:
            return tid
    return None


async def route(user_idea: str, llm_client: Any) -> str | None:
    """主入口: 把 user_idea 路由到模板 id (或 None = 走通用生成)."""
    messages = build_classify_messages(user_idea)
    try:
        response = await llm_client.create_message(
            messages=messages,
            tier="haiku",  # 分类任务简单, 用便宜 model
            max_tokens=50,
            temperature=0.0,
        )
    except Exception as e:
        log.warning("intent_route_failed", user_idea=user_idea[:100], error=str(e))
        return None

    template_id = parse_classify_response(response.content)
    log.info("intent_routed", user_idea=user_idea[:100], template_id=template_id)
    return template_id


# 同步版本 (用于测试)
def route_sync_stub(user_idea: str) -> str | None:
    """简单关键词匹配, 用于测试和兜底 (LLM 不可用时)."""
    idea_lower = user_idea.lower()
    for t in TEMPLATES:
        for kw in t.intent_examples:
            if kw.lower() in idea_lower:
                return t.id
    return None

"""Design Agent - 从 PRD 生成 UI 设计规范 (design_spec.json).

职责: 把"做什么" (设计决策: 颜色/字体/间距/组件) 从 Frontend Agent 拆出来.
Frontend Agent 后续消费 design_spec 决定怎么实现.

输出: design_spec.json
- palette: 配色 (Tailwind 内置颜色, 不用 arbitrary values)
- typography: 字体大小/粗细
- spacing: 间距/容器/卡片
- components: 按钮/输入框/卡片的标准 Tailwind class 模板
- mood: 设计风格关键词 (modern/minimal/playful/corporate)
"""
from __future__ import annotations

import json
from typing import Any

from app.agent.projects.base_agent import AgentAction, AgentState, ProjectAgent
from app.agent.projects.utils import llm_chat
from app.core.logging import get_logger

log = get_logger(__name__)


class DesignAgent(ProjectAgent):
    role = "design"

    def __init__(self, project_id: int, context: dict[str, Any]):
        super().__init__(project_id, context)
        self.prd = context.get("prd", "")
        self.user_idea = context.get("user_idea", "")
        self.design_spec: dict | None = None
        self.files: dict[str, str] = {}

    async def perceive(self) -> AgentState:
        return AgentState(
            project_id=self.project_id,
            role=self.role,
            data={"prd": self.prd, "user_idea": self.user_idea, "design_spec": self.design_spec},
        )

    async def reason(self, state: AgentState) -> AgentAction:
        if self.design_spec is None:
            return AgentAction(type="GENERATE_DESIGN", payload=state.data)
        return AgentAction(type="WAIT")

    async def act(self, action: AgentAction) -> None:
        if action.type == "GENERATE_DESIGN":
            log.info("design_generating", project_id=self.project_id)
            try:
                raw = await llm_chat(
                    system=self._system_prompt(),
                    user=self._build_prompt(),
                    temperature=0.4,
                    llm=self.llm,
                )
                self.design_spec = self._parse_spec(raw)
                if self.design_spec is None:
                    raise ValueError("LLM returned no valid JSON design spec")
            except Exception as e:
                log.warning("design_generation_failed_use_default", error=str(e))
                self.design_spec = self._default_spec()

            # 同时物化到 self.files 供 FrontendAgent 读取和持久化
            self.files["design_spec.json"] = json.dumps(self.design_spec, indent=2, ensure_ascii=False)

            self.record_action("GENERATE_DESIGN")
            await self.save_memory(
                observation=f"生成 design_spec: mood={self.design_spec.get('mood', '?')}, primary={self.design_spec.get('palette', {}).get('primary', '?')}",
                insight="Design Agent 拆出来后, Frontend Agent 可以按 design_spec 直接照搬 class",
                importance=7,
            )
            self.mark_done()

    def _system_prompt(self) -> str:
        return """你是一位资深 UI 设计师. 根据产品需求输出严格的 JSON 格式 design_spec.

**重要约束 (必须遵守, 违反则视为失败)**:
- 所有颜色必须是 Tailwind CSS 内置 class, 格式如 "blue-500" "gray-100" "emerald-500"
- 绝对禁止: 十六进制颜色 (#xxx), rgb(), 颜色名称 ("red", "blue")
- 所有 typography/spacing 字段必须是完整的 Tailwind class 字符串
- components 里的 class 模板用 {primary} {primary_hover} 等占位符

风格选择规则:
- 商务/工具类 → 主色 blue-500 或 slate-500, mood "modern minimal clean"
- 创意/生活类 → 主色 emerald-500 或 amber-500, mood "warm friendly"
- 数据/分析类 → 主色 indigo-500 或 slate-700, mood "professional serious"
- 娱乐/游戏类 → 主色 purple-500 或 pink-500, mood "playful vibrant"
- 夜间/时钟/深色主题 → 主色 amber-500 或 yellow-500, mood "elegant dark" 背景 gray-900

【输出示例 (这是正确格式, 必须模仿)】
```json
{
  "palette": {
    "primary": "blue-500",
    "primary_hover": "blue-600",
    "secondary": "gray-100",
    "accent": "emerald-500",
    "danger": "red-500",
    "background": "gray-50",
    "text": "gray-900",
    "text_muted": "gray-500"
  },
  "typography": {
    "h1": "text-3xl font-bold",
    "h2": "text-2xl font-semibold",
    "h3": "text-lg font-medium",
    "body": "text-base",
    "small": "text-sm"
  },
  "spacing": {
    "container": "max-w-2xl mx-auto p-6 sm:p-8",
    "section_gap": "space-y-6",
    "list_gap": "space-y-2",
    "card_padding": "p-4"
  },
  "components": {
    "button_primary": "px-6 py-2 bg-{primary} text-white rounded-lg hover:bg-{primary_hover} transition disabled:opacity-50",
    "button_secondary": "px-4 py-2 bg-{secondary} text-{text} rounded-lg hover:bg-gray-200 transition",
    "input": "px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-{primary} focus:border-transparent",
    "card": "p-4 bg-white rounded-xl shadow-sm border border-gray-200",
    "list_item": "p-3 bg-white rounded-lg shadow-sm border border-gray-100 flex items-center gap-3"
  },
  "mood": "modern minimal clean"
}
```

**只输出 JSON, 不输出其他任何文字**. 不要 markdown 代码块标记, 不要解释."""

    def _build_prompt(self) -> str:
        parts = ["产品需求 (PRD):", self.prd, "\n用户原始输入:", self.user_idea]
        parts.append("\n请根据以上需求, 决定这个产品的视觉风格, 输出严格 JSON 格式的 design_spec.")
        return "\n".join(parts)

    def _parse_spec(self, raw: str) -> dict | None:
        """从 LLM 输出解析 JSON. 容忍 markdown fence 和前导/后置文本."""
        text = raw.strip()
        # 去掉 markdown code fence
        if text.startswith("```"):
            lines = text.split("\n")
            # 去掉首行 ```json 和末行 ```
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()
        try:
            spec = json.loads(text)
            # 简单校验: 必要字段
            if not isinstance(spec, dict):
                return None
            if "palette" not in spec or "primary" not in spec.get("palette", {}):
                return None
            return spec
        except json.JSONDecodeError:
            # 尝试提取第一个 { 到最后一个 } 之间的内容
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    return None
            return None

    def _default_spec(self) -> dict:
        """LLM 失败时的兜底设计规范."""
        return {
            "palette": {
                "primary": "blue-500",
                "primary_hover": "blue-600",
                "secondary": "gray-100",
                "accent": "emerald-500",
                "danger": "red-500",
                "background": "gray-50",
                "text": "gray-900",
                "text_muted": "gray-500",
            },
            "typography": {
                "h1": "text-3xl font-bold",
                "h2": "text-2xl font-semibold",
                "h3": "text-lg font-medium",
                "body": "text-base",
                "small": "text-sm",
            },
            "spacing": {
                "container": "max-w-2xl mx-auto p-6 sm:p-8",
                "section_gap": "space-y-6",
                "list_gap": "space-y-2",
                "card_padding": "p-4",
            },
            "components": {
                "button_primary": "px-6 py-2 bg-{primary} text-white rounded-lg hover:bg-{primary_hover} transition disabled:opacity-50",
                "button_secondary": "px-4 py-2 bg-{secondary} text-{text} rounded-lg hover:bg-gray-200 transition",
                "input": "px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-{primary} focus:border-transparent",
                "card": "p-4 bg-white rounded-xl shadow-sm border border-gray-200",
                "list_item": "p-3 bg-white rounded-lg shadow-sm border border-gray-100 flex items-center gap-3",
            },
            "mood": "modern minimal clean",
        }

    def get_design_spec(self) -> dict | None:
        return self.design_spec

    def get_files(self) -> dict[str, str]:
        return self.files

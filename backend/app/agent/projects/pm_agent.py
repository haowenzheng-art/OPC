"""PM Agent - 基于用户需求或 workflow plan 生成 PRD."""
from __future__ import annotations

from typing import Any

from app.agent.projects.base_agent import AgentAction, AgentState, ProjectAgent
from app.agent.projects.utils import llm_chat
from app.core.logging import get_logger

log = get_logger(__name__)


class PMAgent(ProjectAgent):
    role = "pm"

    def __init__(self, project_id: int, context: dict[str, Any]):
        super().__init__(project_id, context)
        self.user_idea = context.get("user_idea", "")
        self.workflow_plan = context.get("workflow_plan", "")
        self.prd = ""

    async def perceive(self) -> AgentState:
        return AgentState(
            project_id=self.project_id,
            role=self.role,
            data={"user_idea": self.user_idea, "workflow_plan": self.workflow_plan, "prd": self.prd},
        )

    async def reason(self, state: AgentState) -> AgentAction:
        if not self.prd:
            return AgentAction(type="WRITE_PRD", payload=state.data)
        return AgentAction(type="WAIT")

    async def act(self, action: AgentAction) -> None:
        if action.type == "WRITE_PRD":
            log.info("pm_writing_prd", project_id=self.project_id)
            prompt = self._build_prompt()
            self.prd = await llm_chat(
                system=self._system_prompt(),
                user=prompt,
                temperature=0.3,
                llm=self.llm,
            )
            self.record_action("WRITE_PRD")
            await self.save_memory(
                observation=f"基于需求生成 PRD: {self.user_idea[:80]}",
                insight="使用 LLM 一次性输出结构化 PRD",
                importance=8,
            )
            self.mark_done()

    def _system_prompt(self) -> str:
        return """你是一位资深产品经理。请根据用户需求或工作流优化方案, 输出一份简洁但完整的产品需求文档(PRD)。

PRD 必须包含以下章节:
1. 产品概述 (1-2 句话)
2. 核心功能 (3-5 条)
3. 技术栈 (前端、后端、数据库)
4. 页面/模块清单
5. API 需求清单 (实体 + 主要端点)
6. 数据模型 (主要字段)

请用 Markdown 格式输出, 语言与用户输入保持一致。"""

    def _build_prompt(self) -> str:
        parts = ["用户需求:"]
        parts.append(self.user_idea)
        if self.workflow_plan:
            parts.append("\nWorkflow Thief Arena 分析后的 MVP 计划:")
            parts.append(self.workflow_plan)
        parts.append("\n请根据以上信息生成 PRD。")
        return "\n".join(parts)

    def get_prd(self) -> str:
        return self.prd

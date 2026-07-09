"""CEO Agent - 项目协调者, 制定策略并与其他 Agent 通信."""
from __future__ import annotations

from typing import Any

from app.agent.projects.base_agent import AgentAction, AgentState, ProjectAgent
from app.core.logging import get_logger

log = get_logger(__name__)


class CeoAgent(ProjectAgent):
    role = "ceo"

    def __init__(self, project_id: int, context: dict[str, Any]):
        super().__init__(project_id, context)
        self.user_idea = context.get("user_idea", "")
        self.workflow_plan = context.get("workflow_plan", "")
        self.strategy = ""

    async def perceive(self) -> AgentState:
        return AgentState(
            project_id=self.project_id,
            role=self.role,
            data={"user_idea": self.user_idea, "workflow_plan": self.workflow_plan},
        )

    async def reason(self, state: AgentState) -> AgentAction:
        if not self.strategy:
            return AgentAction(type="PLAN_STRATEGY", payload=state.data)
        return AgentAction(type="WAIT")

    async def act(self, action: AgentAction) -> None:
        if action.type == "PLAN_STRATEGY":
            log.info("ceo_planning", project_id=self.project_id)
            if self.workflow_plan:
                self.strategy = f"基于 Workflow Thief Arena 分析的 MVP 计划生成项目: {self.workflow_plan[:200]}..."
            else:
                self.strategy = f"基于用户需求直接生成项目: {self.user_idea}"

            self.record_action("PLAN_STRATEGY")
            await self.save_memory(
                observation=f"制定项目策略: {self.strategy[:100]}",
                insight="WTA 的分析结果能显著提升生成方向准确性",
                importance=8,
            )
            self.mark_done()

    def get_strategy(self) -> str:
        return self.strategy

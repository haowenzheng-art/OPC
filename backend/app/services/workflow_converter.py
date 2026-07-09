"""Workflow Thief Arena 输出 → OPC MVP 计划转换器.

把 WTA 的 workflow/pain_points/agent_flow/roi 转换成 OPC 能理解的 MVP 计划.
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)


def build_mvp_plan_from_workflow(
    query: str,
    workflow: dict[str, Any],
    pain_points: list[dict[str, Any]],
    agent_flow: dict[str, Any],
    roi: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从 WTA 分析结果构建 MVP 计划."""
    steps = workflow.get("steps", [])
    step_names = [s.get("name", f"步骤 {i + 1}") for i, s in enumerate(steps)]

    # 提取高优先级痛点
    top_pain_points = [
        p.get("description", "")
        for p in pain_points[:3]
    ]

    # 提取 AI 介入点
    interventions = agent_flow.get("interventions", [])
    automation_points = [
        i.get("step", "")
        for i in interventions
        if not i.get("human_approval", False)
    ][:3]

    return {
        "product_name": f"{query} MVP",
        "overview": f"基于 Workflow Thief Arena 分析的 '{query}' 自动化方案",
        "target_workflow_steps": step_names,
        "pain_points_to_solve": top_pain_points,
        "automation_points": automation_points,
        "core_features": _infer_core_features(steps, interventions),
        "tech_stack": {
            "frontend": "Next.js + Tailwind CSS",
            "backend": "Express + TypeScript + Prisma + SQLite",
            "deployment": "Docker Compose",
        },
        "success_metrics": _extract_metrics(roi),
    }


def _infer_core_features(
    steps: list[dict[str, Any]],
    interventions: list[dict[str, Any]],
) -> list[str]:
    """根据工作流步骤和介入点推断核心功能."""
    features = []
    for step in steps[:5]:
        name = step.get("name", "")
        if any(k in name for k in ["收集", "录入", "提交", "创建"]):
            features.append(f"{name} 表单/入口")
        elif any(k in name for k in ["审批", "审核", "决策"]):
            features.append(f"{name} 工作台")
        elif any(k in name for k in ["通知", "提醒", "同步"]):
            features.append(f"{name} 自动化")
        elif any(k in name for k in ["报表", "统计", "分析"]):
            features.append(f"{name} 看板")
    if not features:
        features = ["数据录入", "列表展示", "状态管理"]
    return features[:5]


def _extract_metrics(roi: dict[str, Any] | None) -> list[str]:
    if not roi:
        return ["减少人工重复操作", "提升处理速度"]
    metrics = []
    if "annual_savings" in roi:
        metrics.append(f"年化节省: {roi['annual_savings']}")
    if "roi_percentage" in roi:
        metrics.append(f"ROI: {roi['roi_percentage']}")
    if "time_reduction" in roi:
        metrics.append(f"时间节省: {roi['time_reduction']}")
    return metrics or ["减少人工重复操作", "提升处理速度"]


def mvp_plan_to_text(plan: dict[str, Any]) -> str:
    """把 MVP 计划转成文本, 喂给 OPC PM Agent."""
    lines = [
        f"# {plan.get('product_name', 'MVP')}",
        "",
        f"## 概述\n{plan.get('overview', '')}",
        "",
        "## 目标工作流步骤",
        *[f"- {s}" for s in plan.get("target_workflow_steps", [])],
        "",
        "## 要解决的痛点",
        *[f"- {p}" for p in plan.get("pain_points_to_solve", [])],
        "",
        "## 自动化切入点",
        *[f"- {a}" for a in plan.get("automation_points", [])],
        "",
        "## 核心功能",
        *[f"- {f}" for f in plan.get("core_features", [])],
        "",
        "## 技术栈",
        f"- 前端: {plan.get('tech_stack', {}).get('frontend', 'Next.js')}",
        f"- 后端: {plan.get('tech_stack', {}).get('backend', 'Express')}",
        f"- 部署: {plan.get('tech_stack', {}).get('deployment', 'Docker')}",
        "",
        "## 成功指标",
        *[f"- {m}" for m in plan.get("success_metrics", [])],
    ]
    return "\n".join(lines)

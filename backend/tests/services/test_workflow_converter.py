"""测试 workflow → OPC MVP 计划转换."""
from __future__ import annotations

from app.services.workflow_converter import build_mvp_plan_from_workflow, mvp_plan_to_text


def test_build_mvp_plan_from_workflow():
    workflow = {
        "steps": [
            {"name": "提交申请", "description": "用户提交表单"},
            {"name": "人工审批", "description": "管理员审批"},
            {"name": "发送通知", "description": "系统发送邮件"},
        ]
    }
    pain_points = [
        {"description": "人工审批耗时久"},
        {"description": "通知容易遗漏"},
    ]
    agent_flow = {
        "interventions": [
            {"step": "人工审批", "ai_action": "自动预审", "human_approval": False},
            {"step": "发送通知", "ai_action": "自动发送", "human_approval": False},
        ]
    }
    roi = {"annual_savings": "$10k", "roi_percentage": "200%"}

    plan = build_mvp_plan_from_workflow("保险理赔流程", workflow, pain_points, agent_flow, roi)

    assert plan["product_name"] == "保险理赔流程 MVP"
    assert "提交申请" in plan["target_workflow_steps"]
    assert len(plan["pain_points_to_solve"]) == 2
    assert "人工审批" in plan["automation_points"]
    assert len(plan["core_features"]) > 0
    assert "Next.js" in plan["tech_stack"]["frontend"]


def test_mvp_plan_to_text():
    plan = {
        "product_name": "Test MVP",
        "overview": "Test overview",
        "target_workflow_steps": ["Step 1"],
        "pain_points_to_solve": ["Pain 1"],
        "automation_points": ["Auto 1"],
        "core_features": ["Feature 1"],
        "tech_stack": {"frontend": "Next.js", "backend": "Express", "deployment": "Docker"},
        "success_metrics": ["Metric 1"],
    }
    text = mvp_plan_to_text(plan)
    assert "# Test MVP" in text
    assert "Step 1" in text
    assert "Feature 1" in text
    assert "Next.js" in text

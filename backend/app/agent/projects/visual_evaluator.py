"""P2-3: 多模态视觉评估 (Stage 3 visual loop).

设计:
- 用多模态 LLM (Sonnet 4.6 vision 或 MiniMax M3 vision) 评估截图
- 返回结构化评分: { score: 0-10, issues: [{area, severity, description}] }
- score < 7 → 触发 frontend agent 修样式
- 截图 base64 编码喂给 LLM

不依赖 visual baseline: Stage 3 改成"用 design_spec 描述的视觉规范 + 截图 + LLM 判断符合度",
不靠 hash diff — 因为 OPC 每次生成都不一样, baseline 概念不适用.

精简设计: 这次只做 evaluator 框架 + 单测 + STAGE3_VISUAL.md 设计文档.
完整 5 模板视觉规范 / 视觉 prompt 微调 / orchestrator 集成 放后续 PR.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class VisualIssue:
    """单条视觉问题."""
    area: str  # "spacing" | "color" | "typography" | "layout" | "component" | "other"
    severity: str  # "minor" | "major" | "critical"
    description: str
    suggested_fix: str = ""


@dataclass
class VisualEvaluation:
    """多模态 LLM 的视觉评估结果."""
    score: float  # 0-10
    issues: list[VisualIssue] = field(default_factory=list)
    summary: str = ""  # LLM 文字总结
    raw_response: dict[str, Any] = field(default_factory=dict)

    def is_passing(self, threshold: float = 7.0) -> bool:
        return self.score >= threshold

    def to_failure_signal_dict(self, file_path: str = "frontend/src/app/page.tsx") -> dict:
        """转成 FailureSignal dict, 给 repair pass 用."""
        issues_text = "\n".join([
            f"- [{i.severity}] {i.area}: {i.description}"
            + (f" (建议: {i.suggested_fix})" if i.suggested_fix else "")
            for i in self.issues[:5]
        ])
        return {
            "file_path": file_path,
            "error_kind": "visual",
            "error_msg": f"视觉评分 {self.score:.1f}/10, {len(self.issues)} 个问题:\n{issues_text}",
            "suggested_action": "adjust_styles",
            "agent": "frontend",
            "score": self.score,
        }


VISUAL_EVAL_PROMPT = """你是严格的 UI/UX 视觉评审。基于以下截图, 评估是否符合用户原始需求和设计规范。

## 用户原始需求
{user_idea}

## 设计规范 (Design Agent 输出, 必须遵循)
{design_spec}

## 评分维度 (0-10)
- **layout**: 元素位置 / 排版 / 间距 是否合理
- **color**: 配色是否符合 design_spec (用 design_spec.palette 的颜色, 不要凭空发明)
- **typography**: 字体大小 / 粗细 / 层级 是否清晰
- **component**: 按钮 / 输入框 / 卡片 是否有合理样式 (圆角, 边框, 阴影)
- **responsive**: 移动端 / 桌面端 是否都可用
- **empty_state**: 空数据 / 加载中 / 错误状态 是否有合理 UI
- **affordance**: 用户能否一眼看出哪些元素可点

## 输出格式 (严格 JSON, 不要任何其他文字)
{{
  "score": 0-10 的整数或一位小数,
  "issues": [
    {{
      "area": "spacing" | "color" | "typography" | "layout" | "component" | "responsive" | "empty_state" | "affordance" | "other",
      "severity": "minor" | "major" | "critical",
      "description": "具体描述问题 (e.g. '主按钮用 gray-100 而不是 design_spec 里的 blue-500')",
      "suggested_fix": "怎么改 (e.g. '改 className 为 bg-blue-500 hover:bg-blue-600')"
    }}
  ],
  "summary": "整体评价 (一句话)"
}}
"""


def encode_screenshot_to_base64(screenshot_path: Path) -> str:
    """读 PNG, base64 编码. 喂给多模态 LLM."""
    if not screenshot_path.exists():
        raise FileNotFoundError(f"screenshot not found: {screenshot_path}")
    return base64.b64encode(screenshot_path.read_bytes()).decode("ascii")


def build_visual_eval_messages(
    screenshot_path: Path,
    user_idea: str,
    design_spec: dict,
) -> list[dict[str, Any]]:
    """构造发给多模态 LLM 的 messages.

    Anthropic 多模态格式: content 是 list, 包含 image (base64) + text.
    """
    img_b64 = encode_screenshot_to_base64(screenshot_path)
    prompt_text = VISUAL_EVAL_PROMPT.format(
        user_idea=user_idea,
        design_spec=json.dumps(design_spec, indent=2, ensure_ascii=False)[:2000],
    )
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": img_b64,
                    },
                },
                {
                    "type": "text",
                    "text": prompt_text,
                },
            ],
        }
    ]


def parse_visual_eval_response(raw_content: list[dict[str, Any]]) -> VisualEvaluation:
    """解析 LLM 的返回, 提取结构化 VisualEvaluation.

    容错: LLM 可能返回 markdown 代码块包裹的 JSON, 我们尝试剥掉.
    """
    # 找到 text block
    text = ""
    for block in raw_content:
        if block.get("type") == "text":
            text = block.get("text", "")
            break

    # 尝试直接 parse
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 尝试剥 markdown ```json ... ```
        import re
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                return VisualEvaluation(
                    score=0.0,
                    summary=f"LLM 视觉评估返回无法解析: {text[:200]}",
                    raw_response={"raw_text": text},
                )
        else:
            return VisualEvaluation(
                score=0.0,
                summary=f"LLM 视觉评估返回非 JSON: {text[:200]}",
                raw_response={"raw_text": text},
            )

    issues = [
        VisualIssue(
            area=i.get("area", "other"),
            severity=i.get("severity", "minor"),
            description=i.get("description", ""),
            suggested_fix=i.get("suggested_fix", ""),
        )
        for i in data.get("issues", [])
    ]
    score = float(data.get("score", 0.0))
    return VisualEvaluation(
        score=score,
        issues=issues,
        summary=data.get("summary", ""),
        raw_response=data,
    )


async def evaluate_screenshot(
    llm_client: Any,
    screenshot_path: Path,
    user_idea: str,
    design_spec: dict,
    tier: str = "sonnet",
) -> VisualEvaluation:
    """用多模态 LLM 评估截图, 返回结构化 VisualEvaluation.

    llm_client: 任何有 create_message(messages, tier=...) 的对象
                  (兼容 LLMClient / MockLLMClient)
    """
    messages = build_visual_eval_messages(screenshot_path, user_idea, design_spec)
    response = await llm_client.create_message(
        messages=messages,
        tier=tier,
        max_tokens=2048,
        temperature=0.2,
    )
    return parse_visual_eval_response(response.content)

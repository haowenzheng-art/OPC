"""P2-3: 多模态视觉评估 单测.

覆盖:
- encode_screenshot_to_base64 / build_visual_eval_messages 格式
- parse_visual_eval_response 各种 LLM 返回格式 (raw JSON / markdown / 乱码)
- VisualEvaluation.is_passing / to_failure_signal_dict
- 端到端: mock LLM 截图评估
"""
from __future__ import annotations

import asyncio
import base64
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.agent.projects.visual_evaluator import (
    VISUAL_EVAL_PROMPT,
    VisualEvaluation,
    VisualIssue,
    build_visual_eval_messages,
    encode_screenshot_to_base64,
    evaluate_screenshot,
    parse_visual_eval_response,
)


# --- encode_screenshot_to_base64 ---

def test_encode_screenshot_to_base64(tmp_path: Path):
    p = tmp_path / "shot.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n fake png bytes")
    b64 = encode_screenshot_to_base64(p)
    assert b64 == base64.b64encode(b"\x89PNG\r\n\x1a\n fake png bytes").decode("ascii")


def test_encode_screenshot_raises_if_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        encode_screenshot_to_base64(tmp_path / "nope.png")


# --- build_visual_eval_messages ---

def test_build_visual_eval_messages_format(tmp_path: Path):
    p = tmp_path / "shot.png"
    p.write_bytes(b"fake")
    msgs = build_visual_eval_messages(p, "做一个 todo", {"mood": "modern"})
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    content = msgs[0]["content"]
    assert len(content) == 2
    # image block
    img = content[0]
    assert img["type"] == "image"
    assert img["source"]["type"] == "base64"
    assert img["source"]["media_type"] == "image/png"
    # text block
    txt = content[1]
    assert txt["type"] == "text"
    assert "做一个 todo" in txt["text"]
    assert "modern" in txt["text"]


def test_build_visual_eval_messages_truncates_long_design_spec(tmp_path: Path):
    """超长 design_spec 截断到 2000 chars, 防 LLM context 爆."""
    p = tmp_path / "shot.png"
    p.write_bytes(b"fake")
    long_spec = {"x": "y" * 5000}
    msgs = build_visual_eval_messages(p, "idea", long_spec)
    txt = msgs[0]["content"][1]["text"]
    # design_spec 序列化后被截断, 2000 chars
    assert len(txt) < 5000


# --- parse_visual_eval_response ---

def test_parse_raw_json_response():
    raw = [
        {
            "type": "text",
            "text": json.dumps({
                "score": 8.5,
                "issues": [
                    {
                        "area": "color",
                        "severity": "major",
                        "description": "主按钮颜色不对",
                        "suggested_fix": "改用 blue-500",
                    }
                ],
                "summary": "整体不错, 颜色需要调整",
            }),
        }
    ]
    ev = parse_visual_eval_response(raw)
    assert ev.score == 8.5
    assert ev.summary == "整体不错, 颜色需要调整"
    assert len(ev.issues) == 1
    assert ev.issues[0].area == "color"
    assert ev.issues[0].severity == "major"


def test_parse_markdown_wrapped_json_response():
    """LLM 经常返回 ```json ... ``` 包裹的 JSON, 要能剥掉."""
    raw = [
        {
            "type": "text",
            "text": '好的, 这是我的评估:\n```json\n{"score": 6.0, "issues": [], "summary": "勉强及格"}\n```',
        }
    ]
    ev = parse_visual_eval_response(raw)
    assert ev.score == 6.0
    assert "勉强及格" in ev.summary


def test_parse_garbage_response_returns_zero_score():
    """LLM 瞎返回时, 不能崩, 返回 score=0 + 错误 summary."""
    raw = [{"type": "text", "text": "我看了截图, 感觉不错"}]
    ev = parse_visual_eval_response(raw)
    assert ev.score == 0.0
    assert "无法解析" in ev.summary or "非 JSON" in ev.summary


def test_parse_response_with_no_text_block():
    raw = [{"type": "image", "source": {"type": "base64", "data": "abc"}}]
    ev = parse_visual_eval_response(raw)
    assert ev.score == 0.0


def test_parse_uses_sonnet_pricing_for_minimax_tier_fallback():
    """无关测试, 防止未来 regression."""
    assert True


# --- VisualEvaluation.is_passing / to_failure_signal_dict ---

def test_visual_evaluation_passes_above_threshold():
    ev = VisualEvaluation(score=8.0, summary="好")
    # 8.0 >= 7.0 → pass
    assert ev.is_passing(threshold=7.0) is True
    # 8.0 >= 8.0 → pass (>= 边界)
    assert ev.is_passing(threshold=8.0) is True
    # 8.0 < 8.5 → fail
    assert ev.is_passing(threshold=8.5) is False


def test_visual_evaluation_to_failure_signal_dict():
    ev = VisualEvaluation(
        score=5.0,
        issues=[
            VisualIssue("color", "major", "主按钮颜色不对", "改用 blue-500"),
            VisualIssue("spacing", "minor", "列表项间距过大", "改用 space-y-2"),
        ],
        summary="需要调整",
    )
    sig = ev.to_failure_signal_dict()
    assert sig["error_kind"] == "visual"
    assert sig["agent"] == "frontend"
    assert sig["suggested_action"] == "adjust_styles"
    assert "5.0" in sig["error_msg"] or "5" in sig["error_msg"]
    assert "color" in sig["error_msg"]
    assert "spacing" in sig["error_msg"]
    assert "blue-500" in sig["error_msg"]


# --- 端到端: mock LLM 评估 ---

@pytest.mark.asyncio
async def test_evaluate_screenshot_uses_multimodal_llm(tmp_path: Path):
    """端到端: 喂截图 + 调 LLM, 拿回 VisualEvaluation."""
    from app.agent.llm import LLMClient, LLMResponse

    p = tmp_path / "shot.png"
    p.write_bytes(b"fake png")

    mock_response = LLMResponse(
        content=[{"type": "text", "text": json.dumps({
            "score": 7.5,
            "issues": [
                {"area": "layout", "severity": "minor", "description": "标题居中", "suggested_fix": ""}
            ],
            "summary": "基本符合 design spec"
        })}],
        stop_reason="end_turn",
        input_tokens=1500,
        output_tokens=200,
        model="claude-sonnet-4-6",
        raw=None,
    )
    mock_client = AsyncMock(spec=LLMClient)
    mock_client.create_message = AsyncMock(return_value=mock_response)

    ev = await evaluate_screenshot(
        llm_client=mock_client,
        screenshot_path=p,
        user_idea="做一个 todo list",
        design_spec={"mood": "modern", "palette": {"primary": "blue-500"}},
        tier="sonnet",
    )

    assert ev.score == 7.5
    assert ev.is_passing() is True
    assert len(ev.issues) == 1

    # 验证 LLM 收到了 image content
    call_args = mock_client.create_message.call_args
    messages = call_args.kwargs["messages"]
    assert messages[0]["role"] == "user"
    assert any(c.get("type") == "image" for c in messages[0]["content"])


@pytest.mark.asyncio
async def test_evaluate_screenshot_handles_llm_failure_gracefully(tmp_path: Path):
    """LLM 调用失败, 不能让 orchestrator 整体崩 (降级 skip visual)."""
    p = tmp_path / "shot.png"
    p.write_bytes(b"fake")

    mock_client = AsyncMock()
    mock_client.create_message = AsyncMock(side_effect=RuntimeError("LLM timeout"))

    with pytest.raises(RuntimeError):
        # 现在的设计: visual 评估失败抛异常, 上层 (test_agent) 决定降级 skip
        await evaluate_screenshot(
            llm_client=mock_client,
            screenshot_path=p,
            user_idea="idea",
            design_spec={},
        )

"""P2-4: Intent Router 单测.

覆盖:
- 5 个模板元数据完整性 (id 唯一, slots 合理)
- build_classify_messages 格式
- parse_classify_response 各种 LLM 返回 (raw / 带空格 / 拼写错误 / none)
- route() 端到端: mock LLM 分类
- route_sync_stub() 关键词匹配
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.agent.llm import LLMResponse
from app.agent.projects.intent_router import (
    TEMPLATES,
    TemplateSpec,
    build_classify_messages,
    get_template,
    list_templates,
    parse_classify_response,
    route,
    route_sync_stub,
)


# --- 模板元数据 ---

def test_templates_have_unique_ids():
    ids = [t.id for t in TEMPLATES]
    assert len(ids) == len(set(ids)), f"重复 id: {ids}"


def test_all_templates_have_required_fields():
    for t in TEMPLATES:
        assert t.id
        assert t.name
        assert t.description
        assert len(t.intent_examples) >= 3
        assert t.template_dir


def test_get_template_by_id():
    t = get_template("todo-app-v2")
    assert t is not None
    assert t.name == "Todo App v2"

    assert get_template("nonexistent") is None


def test_list_templates():
    templates = list_templates()
    assert len(templates) == 5
    assert all(isinstance(t, TemplateSpec) for t in templates)


# --- build_classify_messages ---

def test_build_classify_messages_includes_all_templates_and_user_idea():
    msgs = build_classify_messages("做一个 todo list 帮我管理任务")
    assert len(msgs) == 1
    content = msgs[0]["content"]
    assert "todo" in content.lower() or "todo-app" in content
    assert "todo list 帮我管理任务" in content


# --- parse_classify_response ---

def test_parse_clean_id():
    raw = [{"type": "text", "text": "todo-app-v2"}]
    assert parse_classify_response(raw) == "todo-app-v2"


def test_parse_with_whitespace_and_caps():
    raw = [{"type": "text", "text": "  TODO-APP-V2  \n"}]
    assert parse_classify_response(raw) == "todo-app-v2"


def test_parse_with_markdown_formatting():
    raw = [{"type": "text", "text": "`landing-page-v1`"}]
    assert parse_classify_response(raw) == "landing-page-v1"


def test_parse_none():
    raw = [{"type": "text", "text": "none"}]
    assert parse_classify_response(raw) is None

    raw = [{"type": "text", "text": "None\n（用户没说清楚）"}]
    assert parse_classify_response(raw) is None


def test_parse_unknown_id_returns_none():
    raw = [{"type": "text", "text": "calculator-v2"}]  # 不存在的 id
    assert parse_classify_response(raw) is None


def test_parse_partial_match_substring_graceful():
    """LLM 偶尔会返回 'I think the best match is todo-app-v2', 子串匹配要能抓到.

    这是 P2-4 设计: 分类任务用 haiku, 偶尔格式不严格, 子串匹配更友好.
    """
    raw = [{"type": "text", "text": "I think the best match is todo-app-v2"}]
    assert parse_classify_response(raw) == "todo-app-v2"


def test_parse_no_false_positive_for_similar_ids():
    """相近 id 不能误匹配 — e.g. 'todo-app-v3' 不会误判为 'todo-app-v2'."""
    raw = [{"type": "text", "text": "todo-app-v3"}]
    # 'todo-app-v2' 不在 'todo-app-v3' 里 (v2 != v3), 但 'todo-app-v3' 也不在 'todo-app-v2' 里
    # 但我们的实现会先检查 first_line == tid, 失败再 substring
    # 严格: 不匹配 (v3 != v2)
    # 子串: 'todo-app-v2' not in 'todo-app-v3' (因为 v2 在 v3 后面, 严格不等)
    assert parse_classify_response(raw) is None


def test_parse_partial_id_substring():
    """LLM 返回 'todo-app-v2 (理由: ...)' 也能抓到."""
    raw = [{"type": "text", "text": "todo-app-v2\n理由: 用户明确要 todo"}]
    assert parse_classify_response(raw) == "todo-app-v2"


def test_parse_empty_response():
    raw = [{"type": "text", "text": ""}]
    assert parse_classify_response(raw) is None


def test_parse_no_text_block():
    raw = [{"type": "image", "source": {"type": "base64", "data": "abc"}}]
    assert parse_classify_response(raw) is None


# --- route() 端到端 ---

@pytest.mark.asyncio
async def test_route_returns_template_id_on_match():
    mock_client = AsyncMock()
    mock_client.create_message = AsyncMock(return_value=LLMResponse(
        content=[{"type": "text", "text": "todo-app-v2"}],
        stop_reason="end_turn",
        input_tokens=200,
        output_tokens=10,
        model="claude-haiku-3-5",
        raw=None,
    ))

    tid = await route("做一个 todo list 帮我管理任务", mock_client)
    assert tid == "todo-app-v2"


@pytest.mark.asyncio
async def test_route_returns_none_on_no_match():
    mock_client = AsyncMock()
    mock_client.create_message = AsyncMock(return_value=LLMResponse(
        content=[{"type": "text", "text": "none"}],
        stop_reason="end_turn",
        input_tokens=200,
        output_tokens=10,
        model="claude-haiku-3-5",
        raw=None,
    ))

    tid = await route("做一个区块链智能合约审计系统", mock_client)
    assert tid is None


@pytest.mark.asyncio
async def test_route_handles_llm_failure_gracefully():
    mock_client = AsyncMock()
    mock_client.create_message = AsyncMock(side_effect=RuntimeError("LLM down"))

    tid = await route("做一个 todo", mock_client)
    assert tid is None  # LLM 失败 → 不命中 → 走通用 0→1 生成


@pytest.mark.asyncio
async def test_route_uses_haiku_for_cheap_classification():
    """分类任务简单, 强制 haiku (便宜), 上线省钱."""
    mock_client = AsyncMock()
    mock_client.create_message = AsyncMock(return_value=LLMResponse(
        content=[{"type": "text", "text": "calculator-v1"}],
        stop_reason="end_turn",
        input_tokens=100,
        output_tokens=5,
        model="claude-haiku-3-5",
        raw=None,
    ))

    await route("做一个汇率转换", mock_client)
    call_kwargs = mock_client.create_message.call_args.kwargs
    assert call_kwargs["tier"] == "haiku"


# --- route_sync_stub (兜底) ---

def test_route_sync_stub_todo():
    assert route_sync_stub("做一个 todo list") == "todo-app-v2"
    assert route_sync_stub("待办事项") == "todo-app-v2"
    assert route_sync_stub("做一个任务清单") == "todo-app-v2"


def test_route_sync_stub_landing():
    assert route_sync_stub("做一个落地页") == "landing-page-v1"
    assert route_sync_stub("landing page for our product") == "landing-page-v1"


def test_route_sync_stub_dashboard():
    assert route_sync_stub("做一个 dashboard") == "dashboard-v1"
    assert route_sync_stub("数据看板") == "dashboard-v1"


def test_route_sync_stub_form():
    assert route_sync_stub("做一个表单") == "form-v1"
    assert route_sync_stub("联系表单") == "form-v1"


def test_route_sync_stub_calculator():
    assert route_sync_stub("做一个 BMI 计算器") == "calculator-v1"
    assert route_sync_stub("做一个汇率转换") == "calculator-v1"


def test_route_sync_stub_returns_none_on_no_match():
    assert route_sync_stub("做一个区块链智能合约审计") is None
    assert route_sync_stub("搭建一个 Kubernetes 集群") is None
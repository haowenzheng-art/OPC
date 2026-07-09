"""P1-4: LLM cost tracker 单测.

覆盖:
- record() 累加 input/output token + cost_usd
- over_alert_threshold / over_hard_limit
- suggest_tier() 在超阈值时切 haiku, 超硬上限抛 CostHardLimitExceeded
- summary() 格式 (按 tier 分组)
- contextvar: set/reset/get_current_cost_tracker
"""
from __future__ import annotations

import pytest

from app.agent.cost_tracker import (
    CallCost,
    CostHardLimitExceeded,
    CostTracker,
    get_current_cost_tracker,
    reset_current_cost_tracker,
    set_current_cost_tracker,
)


# --- record() 基础累加 ---

def test_record_calculates_cost_correctly():
    """1000 input + 500 output tokens at sonnet 价格应该算出对应 cost."""
    t = CostTracker(project_id=1)
    # sonnet: 3 USD/1M input, 15 USD/1M output
    # 1000 input = 0.003 USD, 500 output = 0.0075 USD, total = 0.0105 USD
    t.record(model="claude-sonnet-4-6", tier="sonnet", input_tokens=1000, output_tokens=500)
    assert abs(t.total_usd - 0.0105) < 1e-6
    assert t.total_input_tokens == 1000
    assert t.total_output_tokens == 500
    assert len(t.calls) == 1


def test_record_accumulates_across_calls():
    """多次 record() 累加."""
    t = CostTracker(project_id=1)
    t.record(model="m", tier="haiku", input_tokens=1000, output_tokens=500)
    t.record(model="m", tier="sonnet", input_tokens=2000, output_tokens=1000)
    assert len(t.calls) == 2
    # haiku: 1/1M * 1000 + 5/1M * 500 = 0.001 + 0.0025 = 0.0035
    # sonnet: 3/1M * 2000 + 15/1M * 1000 = 0.006 + 0.015 = 0.021
    # total = 0.0245
    assert abs(t.total_usd - 0.0245) < 1e-6


def test_record_uses_sonnet_pricing_for_minimax_tier():
    """minimax tier 用 sonnet 等价 (因为没有公开 precise pricing)."""
    t = CostTracker(project_id=1)
    t.record(model="MiniMax-M3", tier="minimax", input_tokens=1_000_000, output_tokens=0)
    # 1M input * 3 USD/1M = 3 USD
    assert abs(t.total_usd - 3.0) < 1e-6


def test_record_unknown_tier_falls_back_to_sonnet():
    """未知 tier 用 sonnet 价, 防崩溃."""
    t = CostTracker(project_id=1)
    t.record(model="m", tier="unknown-tier", input_tokens=1000, output_tokens=500)
    # sonnet pricing applied
    assert abs(t.total_usd - 0.0105) < 1e-6


# --- over_alert_threshold / over_hard_limit ---

def test_over_alert_threshold_default(monkeypatch):
    """默认 alert 0.50, 0.40 不告警, 0.60 告警."""
    from app.config import settings
    monkeypatch.setattr(settings, "cost_alert_threshold_usd", 0.50)
    t = CostTracker(project_id=1)
    # 累积到 0.40 — 不到 0.50 阈值
    t.record(model="m", tier="sonnet", input_tokens=100_000, output_tokens=10_000)
    # 0.10*3 + 0.01*15 = 0.3 + 0.15 = 0.45
    assert t.over_alert_threshold is False
    # 再加 0.30
    t.record(model="m", tier="sonnet", input_tokens=100_000, output_tokens=0)
    # 0.30
    assert t.total_usd > 0.50
    assert t.over_alert_threshold is True


def test_over_hard_limit(monkeypatch):
    """默认 hard limit 2.00, 累计超 2.00 时 over_hard_limit=True."""
    from app.config import settings
    monkeypatch.setattr(settings, "cost_hard_limit_usd", 2.00)
    t = CostTracker(project_id=1)
    # 累积到 1.5 — 不到 2.00
    t.record(model="m", tier="sonnet", input_tokens=500_000, output_tokens=0)
    # 0.5 * 3 = 1.5
    assert t.over_hard_limit is False
    # 再加 1.0
    t.record(model="m", tier="sonnet", input_tokens=333_333, output_tokens=0)
    # ~1.0
    assert t.total_usd > 2.00
    assert t.over_hard_limit is True


# --- suggest_tier() ---

def test_suggest_tier_default_when_under_threshold(monkeypatch):
    """未超阈值时, 用 default tier."""
    from app.config import settings
    monkeypatch.setattr(settings, "cost_alert_threshold_usd", 1.00)
    t = CostTracker(project_id=1)
    # 没 record, total=0
    assert t.suggest_tier("sonnet") == "sonnet"
    assert t.suggest_tier("opus") == "opus"


def test_suggest_tier_switches_to_haiku_when_over_alert(monkeypatch):
    """超 alert threshold 时, sonnet/opus 切 haiku."""
    from app.config import settings
    monkeypatch.setattr(settings, "cost_alert_threshold_usd", 0.10)
    t = CostTracker(project_id=1)
    t.record(model="m", tier="sonnet", input_tokens=100_000, output_tokens=0)
    # 0.3 — 超 0.10
    assert t.suggest_tier("sonnet") == "haiku"
    assert t.suggest_tier("opus") == "haiku"
    # haiku 还是 haiku (已经最低)
    assert t.suggest_tier("haiku") == "haiku"


def test_suggest_tier_raises_when_over_hard_limit(monkeypatch):
    """超硬上限抛 CostHardLimitExceeded."""
    from app.config import settings
    monkeypatch.setattr(settings, "cost_hard_limit_usd", 1.00)
    t = CostTracker(project_id=1)
    t.record(model="m", tier="sonnet", input_tokens=500_000, output_tokens=0)
    # 1.5 — 超 1.00
    with pytest.raises(CostHardLimitExceeded):
        t.suggest_tier("sonnet")


# --- summary() ---

def test_summary_groups_by_tier():
    t = CostTracker(project_id=1)
    t.record(model="m", tier="haiku", input_tokens=1000, output_tokens=500)
    t.record(model="m", tier="haiku", input_tokens=2000, output_tokens=1000)
    t.record(model="m", tier="sonnet", input_tokens=1000, output_tokens=500)
    s = t.summary()
    assert s["total_calls"] == 3
    assert s["total_input_tokens"] == 4000
    assert s["total_output_tokens"] == 2000
    assert s["by_tier"]["haiku"]["calls"] == 2
    assert s["by_tier"]["sonnet"]["calls"] == 1
    assert "over_alert_threshold" in s
    assert "over_hard_limit" in s


# --- contextvar ---

def test_set_get_reset_cost_tracker():
    t = CostTracker(project_id=42)
    assert get_current_cost_tracker() is None

    token = set_current_cost_tracker(t)
    try:
        assert get_current_cost_tracker() is t
    finally:
        reset_current_cost_tracker(token)

    assert get_current_cost_tracker() is None


def test_llm_call_records_to_current_tracker():
    """验证: LLMClient 调完会 record 到当前 contextvar 的 tracker."""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from app.agent.llm import LLMClient, LLMResponse

    t = CostTracker(project_id=42)
    token = set_current_cost_tracker(t)
    try:
        # mock _create_anthropic
        mock_response = LLMResponse(
            content=[],
            stop_reason="end_turn",
            input_tokens=1500,
            output_tokens=200,
            model="claude-sonnet-4-6",
            raw=None,
        )
        with patch.object(LLMClient, "_create_anthropic", new=AsyncMock(return_value=mock_response)):
            client = LLMClient()
            client.provider = "anthropic"
            client._client = None
            asyncio.run(client.create_message(
                messages=[{"role": "user", "content": "hi"}],
                tier="sonnet",
            ))

        # 验证: tracker 收到这次 call
        assert len(t.calls) == 1
        assert t.calls[0].input_tokens == 1500
        assert t.calls[0].output_tokens == 200
    finally:
        reset_current_cost_tracker(token)


def test_llm_call_works_without_tracker_set():
    """无 tracker 时, LLM call 不应崩."""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from app.agent.llm import LLMClient, LLMResponse

    assert get_current_cost_tracker() is None

    mock_response = LLMResponse(
        content=[],
        stop_reason="end_turn",
        input_tokens=100,
        output_tokens=50,
        model="claude-sonnet-4-6",
        raw=None,
    )
    with patch.object(LLMClient, "_create_anthropic", new=AsyncMock(return_value=mock_response)):
        client = LLMClient()
        client.provider = "anthropic"
        client._client = None
        # 不抛异常
        result = asyncio.run(client.create_message(
            messages=[{"role": "user", "content": "hi"}],
            tier="sonnet",
        ))

    assert result.input_tokens == 100
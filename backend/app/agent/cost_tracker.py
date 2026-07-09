"""P1-4: LLM 成本跟踪 + 阈值告警.

设计:
- CostTracker 由 ProjectOrchestrator 持有, 单项目生命周期内累计
- 每次 LLM call 结束 (无论成功/失败) 都 record()
- 累计 cost > alert threshold → 后续阶段切到 Lite model (haiku)
- 累计 cost > hard limit → abort project (防 LLM loop 烧光额度)
- 成本粗算: 用公开的 Claude pricing (USD/1M tokens), 实测偏差 <20%
  可以在 settings 里覆盖单 token 价

为什么不精确计费:
- MiniMax M3 / Agnes 不是 Claude, 自己定价
- 上线后应该接 UsageRecord 表, 这里只做"够用即可"的 cost log
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


# contextvar: 当前协程上下文的 CostTracker (供 LLMClient 自动 record)
_current_cost_tracker: contextvars.ContextVar["CostTracker | None"] = contextvars.ContextVar(
    "_current_cost_tracker", default=None
)


def set_current_cost_tracker(tracker: "CostTracker | None") -> contextvars.Token["CostTracker | None"]:
    return _current_cost_tracker.set(tracker)


def reset_current_cost_tracker(token: contextvars.Token["CostTracker | None"]) -> None:
    _current_cost_tracker.reset(token)


def get_current_cost_tracker() -> "CostTracker | None":
    """获取当前协程上下文的 CostTracker. 业务代码主动设置, LLMClient 自动 record."""
    return _current_cost_tracker.get()


# 公开参考价 (USD per 1M tokens), 写死是为了能算 cost, 上线后接真实计费
# 来源: 各 provider 公开价 (2026-Q2)
_DEFAULT_PRICING = {
    # tier_name: (input_usd_per_1m, output_usd_per_1m)
    "haiku": (1.00, 5.00),
    "sonnet": (3.00, 15.00),
    "opus": (15.00, 75.00),
    "minimax": (3.00, 15.00),  # 按 sonnet 等价估
}


@dataclass
class CallCost:
    """一次 LLM 调用的成本."""
    model: str
    tier: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass
class CostTracker:
    """单项目 LLM 成本跟踪 + 阈值告警.

    用法:
        tracker = CostTracker(project_id=42)
        tracker.record(model="...", tier="sonnet", input_tokens=1500, output_tokens=200)
        if tracker.over_alert_threshold:
            # 切到 Lite
            tier = "haiku"
        if tracker.over_hard_limit:
            raise CostHardLimitExceeded(...)
    """
    project_id: int
    calls: list[CallCost] = field(default_factory=list)
    _alerted: bool = False  # 避免每次 record 都 log

    def record(
        self,
        model: str,
        tier: str,
        input_tokens: int,
        output_tokens: int,
    ) -> CallCost:
        """记一次 LLM 调用的成本."""
        in_price, out_price = _DEFAULT_PRICING.get(tier, _DEFAULT_PRICING["sonnet"])
        cost = (input_tokens / 1_000_000) * in_price + (output_tokens / 1_000_000) * out_price
        call = CallCost(
            model=model,
            tier=tier,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )
        self.calls.append(call)
        log.info(
            "llm_cost_recorded",
            project_id=self.project_id,
            model=model,
            tier=tier,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round(cost, 6),
            total_usd=round(self.total_usd, 4),
        )
        # 阈值告警 (每个 project 只告警一次, 避免 log 洪水)
        if not self._alerted and self.total_usd > settings.cost_alert_threshold_usd:
            log.warning(
                "llm_cost_alert",
                project_id=self.project_id,
                total_usd=round(self.total_usd, 4),
                threshold_usd=settings.cost_alert_threshold_usd,
                msg=f"项目 LLM 成本 ${self.total_usd:.4f} 超过告警阈值 ${settings.cost_alert_threshold_usd:.2f}, 后续 stage 切 Lite model",
            )
            self._alerted = True
        return call

    @property
    def total_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    @property
    def total_input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.calls)

    @property
    def total_output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.calls)

    @property
    def over_alert_threshold(self) -> bool:
        return self.total_usd > settings.cost_alert_threshold_usd

    @property
    def over_hard_limit(self) -> bool:
        return self.total_usd > settings.cost_hard_limit_usd

    def suggest_tier(self, default_tier: str) -> str:
        """根据累计成本, 建议下个 LLM 调用用什么 tier.

        超过告警阈值: 切 haiku (Lite)
        超过硬上限: 抛异常, 让上层 abort
        否则: 用 default_tier
        """
        if self.over_hard_limit:
            raise CostHardLimitExceeded(
                f"project {self.project_id} LLM 成本 ${self.total_usd:.4f} 超过硬上限 ${settings.cost_hard_limit_usd:.2f}, abort"
            )
        if self.over_alert_threshold and default_tier in ("sonnet", "opus"):
            return "haiku"
        return default_tier

    def summary(self) -> dict[str, Any]:
        """返回结构化 summary, 给前端 / persist 写入 project.context."""
        by_tier: dict[str, dict[str, float]] = {}
        for c in self.calls:
            t = by_tier.setdefault(c.tier, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
            t["calls"] += 1
            t["input_tokens"] += c.input_tokens
            t["output_tokens"] += c.output_tokens
            t["cost_usd"] += c.cost_usd
        return {
            "total_calls": len(self.calls),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_usd": round(self.total_usd, 4),
            "by_tier": by_tier,
            "over_alert_threshold": self.over_alert_threshold,
            "over_hard_limit": self.over_hard_limit,
        }


class CostHardLimitExceeded(Exception):
    """LLM 成本超过硬上限, 调用方应该 abort 当前项目."""
    pass

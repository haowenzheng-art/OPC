"""LLM 客户端封装 - 支持 Anthropic 和 OpenAI 兼容 API (如 Agnes).

设计原则:
1. 不静默 fallback - 失败就抛异常，让上层决定怎么处理
2. 统一接口 - create_message 返回标准化 LLMResponse
3. provider 可切换 - 通过 settings.llm_provider 控制
4. token 持久化 - 返回 input/output tokens 用于计费和审计
"""
from __future__ import annotations

import asyncio
import contextvars
import json
from dataclasses import dataclass
from typing import Any

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from app.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


_current_llm: contextvars.ContextVar["LLMClient | None"] = contextvars.ContextVar(
    "_current_llm", default=None
)


def set_current_llm(llm: "LLMClient | None") -> contextvars.Token["LLMClient | None"]:
    """设置当前上下文的 LLMClient，返回 token 用于恢复."""
    return _current_llm.set(llm)


def reset_current_llm(token: contextvars.Token["LLMClient | None"]) -> None:
    """恢复 contextvar 到 set 之前的状态."""
    _current_llm.reset(token)


def get_current_llm() -> "LLMClient":
    """获取当前上下文的 LLMClient，没设置则返回单例."""
    llm = _current_llm.get()
    if llm is not None:
        return llm
    return LLMClient.get()


@dataclass
class LLMResponse:
    """标准化 LLM 响应."""
    content: list[dict[str, Any]]
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    model: str
    raw: Any


class LLMClient:
    """统一 LLM 客户端 - 支持 Anthropic 和 OpenAI 兼容 provider."""

    _instance: LLMClient | None = None

    def __init__(self) -> None:
        self.provider = settings.llm_provider.lower()

        if self.provider == "anthropic":
            if not settings.anthropic_api_key:
                log.warning("anthropic_api_key_not_set", msg="Anthropic LLM calls will fail")
            self._client = AsyncAnthropic(
                api_key=settings.anthropic_api_key,
                base_url=settings.openai_base_url if settings.openai_base_url else None,
            )
        elif self.provider == "openai":
            if not settings.openai_api_key:
                log.warning("openai_api_key_not_set", msg="OpenAI-compatible LLM calls will fail")
            self._client = AsyncOpenAI(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
            )
        elif self.provider == "minimax":
            if not settings.minimax_api_key:
                log.warning("minimax_api_key_not_set", msg="MiniMax LLM calls will fail")
            # MiniMax M3 用 Anthropic Messages API 协议（URL 路径为 /anthropic）
            self._client = AsyncAnthropic(
                api_key=settings.minimax_api_key,
                base_url=settings.minimax_base_url,
            )
        else:
            raise ValueError(f"unknown llm_provider: {self.provider}, expected anthropic/openai/minimax")

        self._models = {
            "haiku": settings.openai_model if self.provider == "openai" else settings.anthropic_model_haiku,
            "sonnet": settings.openai_model if self.provider == "openai" else settings.anthropic_model_sonnet,
            "opus": settings.openai_model if self.provider == "openai" else settings.anthropic_model_opus,
            # MiniMax 始终使用 minimax_model 配置（不分 tier），tier='minimax' 时取这个值
            "minimax": settings.minimax_model,
        }

    @classmethod
    def get(cls) -> "LLMClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def resolve_model(self, tier: str) -> str:
        if tier not in self._models:
            raise ValueError(f"unknown model tier: {tier}, expected haiku/sonnet/opus/minimax")
        return self._models[tier]

    async def create_message(
        self,
        messages: list[dict[str, Any]],
        *,
        tier: str = "sonnet",
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """非流式调用,返回完整 message. 显式 timeout 防止卡死."""
        model = self.resolve_model(tier)
        timeout_s = settings.llm_call_timeout_seconds

        log.info(
            "llm_call_start",
            provider=self.provider,
            model=model,
            tier=tier,
            message_count=len(messages),
            has_tools=bool(tools),
            temperature=temperature,
            timeout_s=timeout_s,
        )

        try:
            if self.provider in ("anthropic", "minimax"):
                coro = self._create_anthropic(
                    model, messages, system, tools, tool_choice, temperature, max_tokens
                )
            else:
                coro = self._create_openai(
                    model, messages, system, tools, tool_choice, temperature, max_tokens
                )
            response = await asyncio.wait_for(coro, timeout=timeout_s)
        except asyncio.TimeoutError:
            log.error(
                "llm_call_timeout",
                provider=self.provider,
                model=model,
                tier=tier,
                timeout_s=timeout_s,
            )
            raise
        except Exception as e:
            log.error(
                "llm_call_failed",
                provider=self.provider,
                model=model,
                tier=tier,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise

        log.info(
            "llm_call_done",
            provider=self.provider,
            model=response.model,
            stop_reason=response.stop_reason,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )

        # P1-4: 自动把这次 LLM 调用的 token / cost 累加到当前 context 的 CostTracker
        # CostTracker 是可选的 — 没设置就跳过 (e.g. 单元测试场景)
        try:
            from app.agent.cost_tracker import get_current_cost_tracker
            tracker = get_current_cost_tracker()
            if tracker is not None:
                tracker.record(
                    model=response.model,
                    tier=tier,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                )
        except ImportError:
            pass  # cost_tracker 模块不可用 (e.g. 在 tests 里 mock)

        return response

    async def _create_anthropic(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str | None,
        tools: list[dict[str, Any]] | None,
        tool_choice: dict[str, Any] | None,
        temperature: float,
        max_tokens: int | None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens or settings.agent_max_tokens_per_call,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice

        raw = await self._client.messages.create(**kwargs)
        return LLMResponse(
            content=[block.model_dump() for block in raw.content],
            stop_reason=raw.stop_reason,
            input_tokens=raw.usage.input_tokens,
            output_tokens=raw.usage.output_tokens,
            model=raw.model,
            raw=raw,
        )

    async def _create_openai(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str | None,
        tools: list[dict[str, Any]] | None,
        tool_choice: dict[str, Any] | None,
        temperature: float,
        max_tokens: int | None,
    ) -> LLMResponse:
        # OpenAI 格式把 system prompt 放到 messages 里
        openai_messages: list[dict[str, Any]] = list(messages)
        if system:
            openai_messages.insert(0, {"role": "system", "content": system})

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": openai_messages,
            "max_tokens": max_tokens or settings.agent_max_tokens_per_call,
            "temperature": temperature,
        }
        if tools:
            # Anthropic schema: {name, description, input_schema}
            # OpenAI expects: {type: "function", function: {name, description, parameters}}
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["input_schema"],
                    },
                }
                for t in tools
            ]
        if tool_choice:
            kwargs["tool_choice"] = tool_choice

        # Agnes thinking 模式 (通过 extra_body 传递 provider-specific 参数)
        # MiniMax 不走这个路径
        if self.provider == "openai" and settings.openai_enable_thinking:
            kwargs["extra_body"] = {
                "chat_template_kwargs": {
                    "enable_thinking": True,
                    "thinking_budget": settings.openai_thinking_budget_tokens,
                }
            }

        raw = await self._client.chat.completions.create(**kwargs)

        # 统一 content 格式
        content: list[dict[str, Any]] = []
        message = raw.choices[0].message
        if message.content:
            content.append({"type": "text", "text": message.content})
        if message.tool_calls:
            for tc in message.tool_calls:
                # OpenAI returns function.arguments as a JSON string; parse to dict
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}
                content.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": args,
                })

        return LLMResponse(
            content=content,
            stop_reason=raw.choices[0].finish_reason,
            input_tokens=raw.usage.prompt_tokens if raw.usage else 0,
            output_tokens=raw.usage.completion_tokens if raw.usage else 0,
            model=raw.model,
            raw=raw,
        )

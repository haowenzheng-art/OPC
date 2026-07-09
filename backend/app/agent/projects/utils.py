"""OPC Agent 工具函数."""
from __future__ import annotations

from typing import Any

from app.agent.llm import LLMClient, LLMResponse
from app.core.logging import get_logger

log = get_logger(__name__)


async def llm_chat(
    system: str,
    user: str,
    *,
    llm: LLMClient | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> str:
    """简单封装：system + user 直接返回文本."""
    client = llm or LLMClient.get()
    response: LLMResponse = await client.create_message(
        messages=[{"role": "user", "content": user}],
        system=system,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    # 兼容 MockLLMClient (返回单个 dict block) 和真实 LLMResponse
    if isinstance(response, dict):
        return response.get("text", "").strip()

    # Anthropic 返回 content blocks, 取 text 类型拼接
    texts: list[str] = []
    for block in response.content:
        if isinstance(block, dict) and block.get("type") == "text":
            texts.append(block.get("text", ""))
    return "".join(texts).strip()


async def llm_chat_with_tools(
    system: str,
    user: str,
    tool_registry: "ToolRegistry",
    *,
    llm: LLMClient | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    max_iterations: int = 8,
) -> tuple[str, list[dict[str, Any]]]:
    """带工具调用的 LLM 循环。

    流程:
      1. 调 LLM 带 tools
      2. 若响应有 tool_use → 执行工具,append tool_result 到 messages → 回到 1
      3. 若响应只有 text → 返回 (text, tool_history)
      4. 超过 max_iterations → 强制返回 (text_so_far, tool_history)

    返回:
      - final_text: LLM 最后一次返回的纯文本 (无工具调用)
      - tool_history: [{"tool": "edit_file", "input": {...}, "output": "...", "success": True}, ...]
    """
    from app.agent.projects.agent_tools import ToolRegistry  # 局部 import 防循环

    client = llm or LLMClient.get()
    tool_defs = tool_registry.get_anthropic_tools()
    tool_history: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]

    final_text = ""
    for iteration in range(max_iterations):
        response = await client.create_message(
            messages=messages,
            system=system,
            tools=tool_defs,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # 分离 tool_use / text blocks
        tool_uses: list[dict[str, Any]] = []
        text_parts: list[str] = []
        for block in response.content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "tool_use":
                tool_uses.append(block)

        # 把 assistant 整段响应 append 到 messages (Anthropic 协议要求)
        if response.content:
            messages.append({"role": "assistant", "content": response.content})

        if not tool_uses:
            final_text = "".join(text_parts).strip()
            log.info(
                "llm_with_tools_done",
                iterations=iteration + 1,
                tools_used=len(tool_history),
                output_chars=len(final_text),
            )
            return final_text, tool_history

        # 执行所有 tool_use,收集 tool_result
        tool_result_blocks: list[dict[str, Any]] = []
        for tu in tool_uses:
            tool_name = tu.get("name", "")
            tool_input = tu.get("input", {}) or {}
            tool_use_id = tu.get("id", "")
            log.info("tool_call", tool=tool_name, iteration=iteration + 1, keys=list(tool_input.keys()))
            result = await tool_registry.execute(tool_name, tool_input)
            tool_history.append({
                "iteration": iteration + 1,
                "tool": tool_name,
                "input": tool_input,
                "output": result.output[:500],
                "success": result.success,
                "error": result.error,
            })
            tool_result_blocks.append(result.to_anthropic_tool_result(tool_use_id))

        # tool_result 作为 user role 的 content
        messages.append({"role": "user", "content": tool_result_blocks})
        final_text = "".join(text_parts).strip()

    log.warning("llm_with_tools_max_iterations", max_iterations=max_iterations, tools_used=len(tool_history))
    return final_text, tool_history


def extract_code_block(text: str, language: str = "") -> str:
    """从 markdown 文本中提取 ```language ... ``` 代码块."""
    marker = f"```{language}" if language else "```"
    start = text.find(marker)
    if start == -1:
        # 尝试任意代码块
        start = text.find("```")
        if start == -1:
            return text.strip()
    start = text.find("\n", start) + 1
    end = text.find("```", start)
    if end == -1:
        return text[start:].strip()
    return text[start:end].strip()

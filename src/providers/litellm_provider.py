"""LLM provider implementation via LiteLLM."""

import asyncio
import json
import time
from typing import Any

from src.config import get_logger
from src.providers.base import (
    LLMProvider,
    LLMResponse,
    LLMUsage,
    Message,
    ToolCall,
    ToolDef,
)

logger = get_logger(__name__)

# Timeout for a single LLM call (seconds)
_REQUEST_TIMEOUT = 90
# Total timeout including retries (seconds)
_TOTAL_TIMEOUT = 90


class LiteLLMProvider(LLMProvider):
    """LLM provider via LiteLLM — unified interface to 100+ models."""

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        max_retries: int = 3,
        request_timeout: int = _REQUEST_TIMEOUT,
        **kwargs: Any,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._max_retries = max_retries
        self._request_timeout = request_timeout
        self._extra_kwargs = kwargs

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        litellm_messages = self._convert_messages(messages)
        litellm_tools = self._convert_tools(tools) if tools else None

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": litellm_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **self._extra_kwargs,
        }
        if litellm_tools:
            kwargs["tools"] = litellm_tools
        if self._api_key:
            kwargs["api_key"] = self._api_key

        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                return await asyncio.wait_for(
                    self._call_litellm(kwargs),
                    timeout=_TOTAL_TIMEOUT,
                )
            except asyncio.TimeoutError:
                last_error = TimeoutError(
                    f"LLM request timed out after {_TOTAL_TIMEOUT}s"
                )
                logger.error("llm_timeout", model=self._model, attempt=attempt + 1)
                break  # timeout — no point retrying
            except Exception as exc:
                last_error = exc
                wait = min(2**attempt, 30)  # 1, 2, 4, 8, ..., capped at 30s
                # For rate limit (429) wait longer
                if "429" in str(exc) or "rate_limit" in str(exc).lower():
                    wait = max(wait, 15)
                logger.warning(
                    "litellm_retry",
                    model=self._model,
                    attempt=attempt + 1,
                    wait=wait,
                    error=str(exc)[:200],
                )
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(wait)

        raise RuntimeError(
            f"LiteLLM: «TRANSLATED» «TRANSLATED» «TRANSLATED» «TRANSLATED» «TRANSLATED» {self._model} "
            f"«TRANSLATED» {self._max_retries} «TRANSLATED». "
            f"«TRANSLATED» «TRANSLATED»: {last_error}"
        )

    def supports_tools(self) -> bool:
        return True

    @staticmethod
    def _convert_messages(messages: list[Message]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for msg in messages:
            entry: dict[str, Any] = {"role": msg.role.value}
            if msg.content:
                entry["content"] = msg.content
            if msg.tool_call_id is not None:
                entry["tool_call_id"] = msg.tool_call_id
            if msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in msg.tool_calls
                ]
            if msg.name is not None:
                entry["name"] = msg.name
            if msg.reasoning_content is not None:
                entry["reasoning_content"] = msg.reasoning_content
            result.append(entry)
        return result

    @staticmethod
    def _convert_tools(tools: list[ToolDef]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]

    async def _call_litellm(self, kwargs: dict[str, Any]) -> LLMResponse:
        import litellm

        start = time.monotonic()
        response = await litellm.acompletion(**kwargs)
        elapsed = time.monotonic() - start

        choice = response.choices[0]
        message = choice.message

        tool_calls: list[ToolCall] | None = None
        if hasattr(message, "tool_calls") and message.tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=(
                        json.loads(tc.function.arguments)
                        if tc.function.arguments
                        else {}
                    ),
                )
                for tc in message.tool_calls
            ]

        usage = LLMUsage()
        if hasattr(response, "usage") and response.usage:
            usage = LLMUsage(
                prompt_tokens=response.usage.prompt_tokens or 0,
                completion_tokens=response.usage.completion_tokens or 0,
                total_tokens=response.usage.total_tokens or 0,
            )

        logger.info(
            "litellm_call",
            model=self._model,
            elapsed_ms=round(elapsed * 1000),
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            has_tool_calls=bool(tool_calls),
        )

        # Extract reasoning_content (DeepSeek V4 Pro thinking mode)
        reasoning = getattr(message, "reasoning_content", None) or None

        return LLMResponse(
            content=message.content or "",
            tool_calls=tool_calls,
            usage=usage,
            reasoning_content=reasoning,
        )

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


class LiteLLMProvider(LLMProvider):
    """LLM provider via LiteLLM — unified interface to 100+ models.

    Supports cloud token proxy: if cloud_proxy_url and cloud_token are
    provided, routes requests through the Spec Editor Cloud Proxy for
    metered usage instead of calling the LLM provider directly.
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        max_retries: int = 3,
        request_timeout: int = _REQUEST_TIMEOUT,
        cloud_proxy_url: str = "",
        cloud_token: str = "",
        **kwargs: Any,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._max_retries = max_retries
        self._request_timeout = request_timeout
        self._cloud_proxy_url = cloud_proxy_url
        self._cloud_token = cloud_token
        self._extra_kwargs = kwargs

    @property
    def uses_cloud_proxy(self) -> bool:
        """Whether this provider routes through the cloud token proxy."""
        return bool(self._cloud_proxy_url and self._cloud_token)

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        litellm_messages = self._convert_messages(messages)
        litellm_tools = self._convert_tools(tools) if tools else None

        # If cloud proxy is configured, route through it instead of
        # calling the LLM provider directly. The proxy handles metering.
        if self._cloud_proxy_url and self._cloud_token:
            return await self._complete_via_cloud_proxy(
                messages, tools, temperature, max_tokens,
            )

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
                    timeout=_REQUEST_TIMEOUT,
                )
            except asyncio.TimeoutError:
                last_error = TimeoutError(
                    f"LLM request timed out after {_REQUEST_TIMEOUT}s"
                )
                logger.warning(
                    "llm_timeout_retry",
                    model=self._model,
                    attempt=attempt + 1,
                    max_retries=self._max_retries,
                )
                wait = min(2**attempt, 30)
            except Exception as exc:
                last_error = exc
                wait = min(2**attempt, 30)
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
            f"LiteLLM: all retry attempts exhausted for model {self._model} "
            f"after {self._max_retries} attempts. "
            f"Last error: {last_error}"
        )

    def supports_tools(self) -> bool:
        return True

    async def _complete_via_cloud_proxy(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Route the LLM request through the cloud token proxy.

        The proxy meters token usage against the user's cloud token
        balance, forwards to the real LLM provider, and returns the
        response. Uses httpx for async HTTP with retry support.
        """
        import httpx

        litellm_messages = self._convert_messages(messages)
        litellm_tools = self._convert_tools(tools) if tools else None

        body: dict[str, Any] = {
            "model": self._model,
            "messages": litellm_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if litellm_tools:
            body["tools"] = litellm_tools

        headers = {
            "Content-Type": "application/json",
            "X-Cloud-Token": self._cloud_token,
        }

        # If user also has a direct API key, pass it through so the
        # proxy can forward it to the LLM provider (hybrid mode:
        # proxy for metering, user's own key for LLM access).
        if self._api_key:
            headers["X-LLM-API-Key"] = self._api_key

        url = self._cloud_proxy_url.rstrip("/") + "/v1/chat/completions"

        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                async with httpx.AsyncClient(timeout=self._request_timeout) as client:
                    response = await client.post(url, json=body, headers=headers)

                if response.status_code == 402:
                    # Insufficient cloud tokens — don't retry
                    detail = response.json() if response.text else {}
                    msg = detail.get("detail", {}).get("error", "Insufficient cloud tokens")
                    purchase_url = detail.get("detail", {}).get(
                        "purchase_url",
                        "https://gumroad.com/l/spec-editor-cloud",
                    )
                    raise RuntimeError(
                        f"Cloud token balance exhausted. {msg} "
                        f"Top up at: {purchase_url}"
                    )

                if response.status_code == 401:
                    raise RuntimeError(
                        "Invalid cloud token. Check your license key "
                        "or purchase cloud tokens."
                    )

                if response.status_code != 200:
                    error_msg = response.text[:500]
                    logger.warning(
                        "cloud_proxy_error",
                        status=response.status_code,
                        attempt=attempt + 1,
                        error=error_msg,
                    )
                    last_error = RuntimeError(
                        f"Cloud proxy returned {response.status_code}: {error_msg}"
                    )
                    wait = min(2**attempt, 30)
                    if attempt < self._max_retries - 1:
                        await asyncio.sleep(wait)
                    continue

                # Parse the response — it's the raw LLM provider response
                # forwarded by the proxy
                data = response.json()
                return self._parse_response_data(data)

            except (httpx.HTTPError, OSError) as exc:
                last_error = exc
                wait = min(2**attempt, 30)
                logger.warning(
                    "cloud_proxy_retry",
                    attempt=attempt + 1,
                    wait=wait,
                    error=str(exc)[:200],
                )
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(wait)

        raise RuntimeError(
            f"Cloud proxy: all retry attempts exhausted for {url}. "
            f"Last error: {last_error}"
        )

    def _parse_response_data(self, data: dict[str, Any]) -> LLMResponse:
        """Parse LLM response data (from proxy or direct) into LLMResponse."""
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("LLM response has no choices")

        choice = choices[0]
        message = choice.get("message", {})

        tool_calls: list[ToolCall] | None = None
        if message.get("tool_calls"):
            tool_calls = [
                ToolCall(
                    id=tc.get("id", ""),
                    name=tc.get("function", {}).get("name", ""),
                    arguments=(
                        json.loads(tc.get("function", {}).get("arguments", "{}"))
                        if tc.get("function", {}).get("arguments")
                        else {}
                    ),
                )
                for tc in message["tool_calls"]
            ]

        usage_data = data.get("usage", {})
        usage = LLMUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        )

        reasoning = message.get("reasoning_content", None)

        logger.info(
            "litellm_call",
            model=self._model,
            has_tool_calls=bool(tool_calls),
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
        )

        return LLMResponse(
            content=message.get("content", "") or "",
            tool_calls=tool_calls,
            usage=usage,
            reasoning_content=reasoning,
        )

    @staticmethod
    def _convert_messages(messages: list[Message]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for msg in messages:
            # Skip assistant messages with no content AND no tool_calls —
            # DeepSeek/OpenAI APIs reject these with "Invalid assistant message"
            if (
                msg.role.value == "assistant"
                and not msg.content
                and not msg.tool_calls
            ):
                continue
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

"""Ollama LLM provider for local inference on Apple Silicon.

Uses Ollama's HTTP API (http://localhost:11434) for fast local inference
without cloud API keys. Ideal for diagram generation and other non-critical
LLM tasks where latency is acceptable.

Requires: ollama pull qwen2.5-coder:7b
"""

import json
from typing import Any

import aiohttp

from src.config import get_logger
from src.providers.base import (
    LLMProvider,
    LLMResponse,
    LLMUsage,
    Message,
    ToolDef,
)

logger = get_logger(__name__)

_OLLAMA_URL = "http://localhost:11434/api/chat"


class OllamaProvider(LLMProvider):
    """LLM provider via local Ollama server.

    No API key required — runs fully offline on Apple Silicon.
    Supports any model pulled via `ollama pull`.
    """

    def __init__(
        self,
        model: str = "qwen2.5-coder:7b",
        host: str = "http://localhost:11434",
        request_timeout: int = 120,
        **kwargs: Any,
    ) -> None:
        self._model = model
        self._url = f"{host}/api/chat"
        self._request_timeout = request_timeout

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
    ) -> LLMResponse:
        """Send chat completion request to Ollama.

        Ollama doesn't support native tool calling — we convert tools to
        system prompt instructions instead.
        """
        ollama_messages = []
        for msg in messages:
            role = msg.role.value
            if role == "system":
                role = "system"
            elif role == "assistant":
                role = "assistant"
            else:
                role = "user"

            content = msg.content or ""

            # Inject tool descriptions into system prompt
            if role == "system" and tools:
                tool_descriptions = "\n".join(
                    f"- {t.name}: {t.description}" for t in tools
                )
                content += (
                    f"\n\nAvailable tools (you can't call them, "
                    f"just describe what you'd use):\n{tool_descriptions}"
                )

            ollama_messages.append({"role": role, "content": content})

        payload = {
            "model": self._model,
            "messages": ollama_messages,
            "stream": False,
            "options": {
                "temperature": 0.3,
                "num_predict": 2048,
            },
        }

        start_time = __import__("time").monotonic()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self._request_timeout),
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        raise RuntimeError(
                            f"Ollama error {resp.status}: {error_text[:500]}"
                        )
                    data = await resp.json()
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"Ollama connection failed: {exc}") from exc

        elapsed = __import__("time").monotonic() - start_time
        content = data.get("message", {}).get("content", "")

        # Estimate tokens (Ollama doesn't always return usage)
        prompt_tokens = data.get("prompt_eval_count", 0)
        completion_tokens = data.get("eval_count", 0)

        logger.info(
            "ollama_call",
            model=self._model,
            elapsed_ms=int(elapsed * 1000),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

        return LLMResponse(
            content=content,
            tool_calls=None,
            usage=LLMUsage(
                prompt_tokens=prompt_tokens or len(str(messages)) // 4,
                completion_tokens=completion_tokens or len(content) // 4,
                total_tokens=(prompt_tokens or 0) + (completion_tokens or 0),
            ),
        )

    def supports_tools(self) -> bool:
        """Ollama doesn't support native tool calling."""
        return False

    async def complete_with_image(
        self,
        messages: list[Message],
        image_data: str,
        media_type: str = "image/png",
    ) -> LLMResponse:
        """Send a vision request with an image to Ollama.

        Uses Ollama's native image support by attaching base64-encoded
        images to the last user message.
        """
        ollama_messages = []
        for i, msg in enumerate(messages):
            role = msg.role.value
            if role == "system":
                role = "system"
            elif role == "assistant":
                role = "assistant"
            else:
                role = "user"

            if role == "user" and i == len(messages) - 1:
                # Attach image to the last user message
                ollama_messages.append(
                    {
                        "role": "user",
                        "content": msg.content or "",
                        "images": [image_data],
                    }
                )
            else:
                ollama_messages.append(
                    {
                        "role": role,
                        "content": msg.content or "",
                    }
                )

        payload = {
            "model": self._model,
            "messages": ollama_messages,
            "stream": False,
            "options": {
                "temperature": 0.2,
                "num_predict": 1024,
            },
        }

        start_time = __import__("time").monotonic()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self._request_timeout),
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        raise RuntimeError(
                            f"Ollama error {resp.status}: {error_text[:500]}"
                        )
                    data = await resp.json()
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"Ollama connection failed: {exc}") from exc

        elapsed = __import__("time").monotonic() - start_time
        content = data.get("message", {}).get("content", "")

        prompt_tokens = data.get("prompt_eval_count", 0)
        completion_tokens = data.get("eval_count", 0)

        logger.info(
            "ollama_vision_call",
            model=self._model,
            elapsed_ms=int(elapsed * 1000),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

        return LLMResponse(
            content=content,
            tool_calls=None,
            usage=LLMUsage(
                prompt_tokens=prompt_tokens or 500,
                completion_tokens=completion_tokens or len(content) // 4,
                total_tokens=(prompt_tokens or 500)
                + (completion_tokens or len(content) // 4),
            ),
        )

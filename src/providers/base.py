"""Abstract LLM provider interface and message models."""

from abc import ABC, abstractmethod
from enum import Enum

from pydantic import BaseModel, Field

# ------------------------------------------------------------------
# Message models
# ------------------------------------------------------------------


class MessageRole(str, Enum):
    """Role of a message in a dialogue."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCall(BaseModel):
    """Tool call (function calling)."""

    id: str = Field(description="Unique call ID")
    name: str = Field(description="Tool name")
    arguments: dict = Field(
        default_factory=dict,
        description="Call arguments",
    )


class Message(BaseModel):
    """Message in a dialogue with an LLM."""

    role: MessageRole
    content: str = ""
    tool_call_id: str | None = Field(
        default=None,
        description="Call ID (for TOOL messages)",
    )
    tool_calls: list[ToolCall] | None = Field(
        default=None,
        description="Tool calls (for ASSISTANT messages)",
    )
    name: str | None = Field(
        default=None,
        description="Sending agent name (optional)",
    )
    reasoning_content: str | None = Field(
        default=None,
        description="Thinking content (DeepSeek V4 Pro thinking mode)",
    )


# ------------------------------------------------------------------
# Tool definitions
# ------------------------------------------------------------------


class ToolDef(BaseModel):
    """Tool definition in OpenAI function calling format."""

    name: str = Field(description="Unique tool name")
    description: str = Field(description="Description of what the tool does")
    parameters: dict = Field(
        default_factory=dict,
        description="JSON Schema of parameters",
    )


# ------------------------------------------------------------------
# LLM response
# ------------------------------------------------------------------


class LLMUsage(BaseModel):
    """Token usage information."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMResponse(BaseModel):
    """Response from an LLM provider."""

    content: str | None = Field(
        default=None,
        description="Text response (may be None when tool_calls are present)",
    )
    tool_calls: list[ToolCall] | None = Field(
        default=None,
        description="Tool calls requested by the model",
    )
    usage: LLMUsage = Field(default_factory=LLMUsage)
    reasoning_content: str | None = Field(
        default=None, description="Thinking content (DeepSeek V4 Pro)"
    )


# ------------------------------------------------------------------
# Abstract provider
# ------------------------------------------------------------------


class LLMProvider(ABC):
    """Abstract LLM provider.

    Implementations: LiteLLMProvider, and in the future — direct adapters
    for specific APIs.
    """

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send a request to the LLM and get a response.

        Args:
            messages: dialogue history
            tools: available tools (function calling)
            temperature: generation temperature
            max_tokens: maximum number of tokens in the response

        Returns:
            LLMResponse with text and/or tool calls
        """
        ...

    @abstractmethod
    def supports_tools(self) -> bool:
        """Whether the provider supports function calling (tool use)."""
        ...

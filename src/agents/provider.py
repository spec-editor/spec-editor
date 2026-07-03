"""Agent interface — abstract base for agent implementations.

Allows swapping between current LoopAgent, future LangGraphAgent, etc.
"""

from abc import ABC, abstractmethod
from typing import Callable

from src.providers.base import LLMProvider, LLMResponse, Message, ToolDef


class AgentRunResult:
    """Result of a single agent run() call.

    Mirrors LLMResponse but decoupled from provider internals.
    """

    def __init__(
        self,
        content: str = "",
        tool_calls: list | None = None,
    ) -> None:
        self.content = content or ""
        self.tool_calls = tool_calls or []


class AgentProvider(ABC):
    """Abstract agent that can process a task with optional history."""

    def __init__(
        self,
        name: str,
        provider: LLMProvider,
        system_prompt: str,
        tools: list[ToolDef],
        tool_handlers: dict[str, Callable],
    ) -> None:
        self.name = name
        self._provider = provider
        self._system_prompt = system_prompt
        self._tools = tools
        self._tool_handlers = tool_handlers

    @abstractmethod
    async def run(
        self,
        user_message: str,
        conversation_history: list[Message] | None = None,
        trace_callback: Callable[[str], None] | None = None,
    ) -> AgentRunResult:
        """Process a user message and return the agent's response."""
        ...

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """Return the system prompt for introspection."""
        ...

    def get_methodology(self):
        """Return methodology if agent has one (for SpecAgent compatibility)."""
        return getattr(self, "_methodology", None)

    def get_source_dir(self):
        """Return source dir if agent has one."""
        return getattr(self, "_source_dir", None)

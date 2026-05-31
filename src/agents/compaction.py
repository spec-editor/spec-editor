"""Smart context compaction: plan, result, token budget."""

from collections import Counter
from typing import Any

from src.providers.base import Message, MessageRole


class ContextCompactor:
    """Tracks tokens and LLM calls, compacts context meaningfully.

    Three compaction triggers:
    - Auto: > max_llm_calls LLM calls
    - Auto: > token_budget tokens
    - Manual: compact(reason) on agent call
    """

    def __init__(self, max_llm_calls: int = 30, token_budget: int = 50000) -> None:
        self._max_llm_calls = max_llm_calls
        self._token_budget = token_budget
        self._llm_call_count = 0
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        # Statistics for summary
        self._tool_counts: Counter[str] = Counter()
        self._aspect_counts: Counter[str] = Counter()
        self._first_plan: str = ""  # first agent "plan"

    def record_llm_call(self, prompt_tokens: int, completion_tokens: int) -> None:
        """Record one LLM call."""
        self._llm_call_count += 1
        self._total_prompt_tokens += prompt_tokens
        self._total_completion_tokens += completion_tokens

    def record_plan(self, text: str) -> None:
        """Save the agent's "plan" from its message."""
        if not self._first_plan and text:
            self._first_plan = text[:300]

    def record_tool_call(self, tool_name: str, arguments: dict | None = None) -> None:
        """Record a tool call for statistics."""
        self._tool_counts[tool_name] += 1
        if tool_name == "write_element" and arguments:
            aspect = arguments.get("aspect", "?")
            self._aspect_counts[aspect] += 1

    @property
    def total_tokens(self) -> int:
        return self._total_prompt_tokens + self._total_completion_tokens

    @property
    def calls(self) -> int:
        return self._llm_call_count

    def should_compact(self) -> bool:
        """Time to compact?"""
        return (
            self._llm_call_count >= self._max_llm_calls
            or self.total_tokens >= self._token_budget
        )

    def compact(self, messages: list[Message], reason: str = "Completed") -> list[Message]:
        """Compress history into a meaningful summary.

        Keeps: system prompt + summary + last 2 agent messages.
        """
        summary = self._build_summary(reason)
        compacted: list[Message] = []

        # System prompt
        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                compacted.append(msg)
                break

        # Summary
        compacted.append(Message(role=MessageRole.USER, content=summary, name="system"))

        # Last "meaningful" messages
        meaningful = [
            m
            for m in messages
            if m.role in (MessageRole.USER, MessageRole.ASSISTANT)
            and not m.tool_calls
            and m.role != MessageRole.TOOL
            and m.name != "system"
        ]
        compacted.extend(meaningful[-2:])

        # Reset counters for next cycle
        self.reset()

        return compacted

    def reset(self) -> None:
        """Reset counters (after compaction)."""
        self._llm_call_count = 0
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._tool_counts.clear()
        self._aspect_counts.clear()
        self._first_plan = ""

    def _build_summary(self, reason: str) -> str:
        """Build a readable summary of what was done."""
        parts = [f"[Context compacted: {reason}]"]

        if self._first_plan:
            parts.append(f"Plan: {self._first_plan}")

        # Results by aspect
        if self._aspect_counts:
            aspects_str = ", ".join(
                f"{a} (+{c})" for a, c in self._aspect_counts.most_common()
            )
            parts.append(f"Created: {aspects_str}")

        # Relationships and other
        other_tools = {
            k: v
            for k, v in self._tool_counts.items()
            if k
            not in (
                "write_element",
                "read_element",
                "list_all_elements",
                "list_aspect",
                "search_elements",
                "find_related",
            )
        }
        if other_tools:
            other_str = ", ".join(f"{k} x{v}" for k, v in other_tools.items())
            parts.append(f"Operations: {other_str}")

        # Tokens
        parts.append(
            f"Tokens: {self.total_tokens} "
            f"(prompt: {self._total_prompt_tokens}, completion: {self._total_completion_tokens})"
        )

        return "\n".join(parts)

"""Base agent class with LLM and smart context compaction."""

import json
from typing import Any, Callable

from src.agents.compaction import ContextCompactor
from src.config import get_logger
from src.providers.base import (
    LLMProvider,
    LLMResponse,
    LLMUsage,
    Message,
    MessageRole,
    ToolCall,
    ToolDef,
)

logger = get_logger(__name__)


class BaseAgent:
    """Agent with LLM and smart context compaction (ContextCompactor)."""

    def __init__(
        self,
        name: str,
        provider: LLMProvider,
        system_prompt: str,
        tools: list[ToolDef],
        tool_handlers: dict[str, Callable],
        max_llm_calls: int = 30,
        token_budget: int = 50000,
    ) -> None:
        self.name = name
        self._provider = provider
        self._system_prompt = system_prompt
        self._tools = tools
        self._tool_handlers = tool_handlers
        self._compactor = ContextCompactor(
            max_llm_calls=max_llm_calls, token_budget=token_budget
        )
        self._max_llm_calls = max_llm_calls

    async def run(
        self,
        user_message: str,
        conversation_history: list[Message] | None = None,
    ) -> LLMResponse:
        """Send a message to the agent and get a response."""
        messages: list[Message] = []

        if not conversation_history:
            messages.append(
                Message(role=MessageRole.SYSTEM, content=self._system_prompt)
            )
        else:
            has_system = any(m.role == MessageRole.SYSTEM for m in conversation_history)
            if not has_system:
                messages.append(
                    Message(role=MessageRole.SYSTEM, content=self._system_prompt)
                )

            for msg in conversation_history:
                if msg.tool_calls:
                    messages.append(
                        Message(
                            role=msg.role,
                            content=msg.content or "(executed tools)",
                            name=msg.name,
                            reasoning_content=msg.reasoning_content,
                        )
                    )
                else:
                    messages.append(msg)

        messages.append(
            Message(role=MessageRole.USER, content=user_message, name="user")
        )

        tools = self._tools if self._provider.supports_tools() else None
        _total_calls = 0
        _hard_limit = self._max_llm_calls * 3  # hard limit per single run() call
        _total_writes = 0  # total elements + relationships created
        _cost_per_write_limit = 0.02  # $0.02 — raised for early rounds
        _consecutive_noop = 0  # consecutive calls without productive actions
        _cumulative_prompt = 0  # accumulated prompt tokens (not reset)
        _cumulative_completion = 0  # accumulated completion tokens

        while True:
            _total_calls += 1
            if _total_calls > _hard_limit:
                logger.warning(
                    "llm_call_limit_reached",
                    agent=self.name,
                    calls=_total_calls,
                    limit=_hard_limit,
                )
                # Force stop — return last response
                return LLMResponse(
                    content="(LLM call limit reached — ending turn)",
                    usage=LLMUsage(),
                )

            response = await self._provider.complete(messages=messages, tools=tools)

            # Record tokens and plan
            self._compactor.record_llm_call(
                response.usage.prompt_tokens, response.usage.completion_tokens
            )
            _cumulative_prompt += response.usage.prompt_tokens
            _cumulative_completion += response.usage.completion_tokens
            _cumulative_cost = (
                _cumulative_prompt * 0.14 + _cumulative_completion * 0.28
            ) / 1_000_000
            if response.content and not response.tool_calls:
                self._compactor.record_plan(response.content)

            if not response.tool_calls:
                # Final response without tools
                total_all = (
                    self._compactor._total_prompt_tokens
                    + self._compactor._total_completion_tokens
                )
                cost = (
                    self._compactor._total_prompt_tokens * 0.14
                    + self._compactor._total_completion_tokens * 0.28
                ) / 1_000_000
                if response.content:
                    print(
                        f"[{self.name}] Response ({total_all} tok, ~${cost:.4f}): {response.content[:150]}...",
                        flush=True,
                    )
                return response

            # Show what the agent is doing
            tool_names = [tc.name for tc in response.tool_calls]
            writes = [tc for tc in response.tool_calls if tc.name == "write_element"]
            rels = [
                tc
                for tc in response.tool_calls
                if tc.name in ("add_relationship", "remove_relationship")
            ]
            parts = []
            if writes:
                ids = [tc.arguments.get("id", "?") for tc in writes]
                parts.append(
                    f"+{len(writes)} elements ({', '.join(ids[:3])}{'...' if len(ids) > 3 else ''})"
                )
            if rels:
                parts.append(f"{len(rels)} relationships")
            reads = [
                tc
                for tc in response.tool_calls
                if tc.name in ("read_element", "list_aspect", "search_elements")
            ]
            if reads and not writes and not rels:
                parts.append(f"reads {len(reads)} elements")
            other = [
                n
                for n in tool_names
                if n
                not in (
                    "write_element",
                    "add_relationship",
                    "remove_relationship",
                    "read_element",
                    "list_aspect",
                    "search_elements",
                    "list_all_elements",
                    "find_related",
                )
            ]
            if other:
                parts.append(", ".join(other))
            total_all = (
                self._compactor._total_prompt_tokens
                + self._compactor._total_completion_tokens
            )
            cost = (
                self._compactor._total_prompt_tokens * 0.14
                + self._compactor._total_completion_tokens * 0.28
            ) / 1_000_000
            cper = (
                f", ${_cumulative_cost / max(_total_writes, 1):.4f}/write"
                if _total_writes
                else ""
            )
            ctotal = (
                f", total ${_cumulative_cost:.4f}" if _cumulative_cost > 0.01 else ""
            )
            msg = f"[{self.name}] call #{_total_calls} ({total_all} tok, ~${cost:.4f}{cper}{ctotal}): {'; '.join(parts) if parts else ', '.join(tool_names[:5])}"
            print(msg, flush=True)

            assistant_msg = Message(
                role=MessageRole.ASSISTANT,
                content=response.content or "",
                tool_calls=response.tool_calls,
                name=self.name,
                reasoning_content=response.reasoning_content,
            )
            messages.append(assistant_msg)

            # Count writes in this batch
            batch_writes = sum(
                1
                for tc in response.tool_calls
                if tc.name
                in ("write_element", "add_relationship", "remove_relationship")
            )
            _total_writes += batch_writes
            if batch_writes > 0:
                _consecutive_noop = 0
            else:
                _consecutive_noop += 1

            tool_results = await self._execute_tools(response.tool_calls)
            messages.extend(tool_results)

            # Check cost threshold
            if _total_writes > 0:
                cost_per_write = _cumulative_cost / _total_writes
                if _total_calls >= 5 and cost_per_write > _cost_per_write_limit:
                    print(
                        f"[{self.name}] ⚠ STOP: cost ${_cumulative_cost:.4f} / {_total_writes} writes = "
                        f"${cost_per_write:.4f}  write ( ${_cost_per_write_limit:.4f})",
                        flush=True,
                    )
                    return LLMResponse(
                        content=f"(:  ${cost_per_write:.4f}/write >  ${_cost_per_write_limit:.4f})",
                        usage=LLMUsage(),
                    )

            # Check "empty" cycle: >20 consecutive calls without writes
            if _consecutive_noop >= 20 and _cumulative_cost > 0.05:
                print(
                    f"[{self.name}] ⚠ : {_consecutive_noop} callagent limit reached writes "
                    f"(${_cumulative_cost:.4f} total )",
                    flush=True,
                )
                return LLMResponse(
                    content=(
                        f"Я   — agent limit reached, "
                        f"connectivity 0.88,  0, agent limit reached. "
                        f"agent limit reached. agent limit reached."
                    ),
                    usage=LLMUsage(),
                )

            # Check: time to compact context?
            if self._compactor.should_compact():
                messages = self._compactor.compact(messages, reason="")
                logger.info(
                    "context_compacted",
                    agent=self.name,
                    calls=self._compactor.calls,
                    tokens=self._compactor.total_tokens,
                )

    def compact_now(self, reason: str = "") -> None:
        """Manual compaction (called by the compact_context tool)."""
        # This method is called externally via _execute_tools
        # Actual compaction happens in run()
        self._compactor._llm_call_count = self._compactor._max_llm_calls  # force

    async def _execute_tools(self, tool_calls: list[ToolCall]) -> list[Message]:
        tool_messages: list[Message] = []

        for tc in tool_calls:
            logger.debug("tool_call", agent=self.name, tool=tc.name)

            handler = self._tool_handlers.get(tc.name)
            if handler is None:
                result = {"error": f"Unknown tool: {tc.name}"}
            else:
                try:
                    result = await _call_handler(handler, tc.arguments)
                except Exception as exc:
                    result = {"error": str(exc)}
                    logger.error(
                        "tool_error", agent=self.name, tool=tc.name, error=str(exc)
                    )

            # Record in summary
            self._compactor.record_tool_call(tc.name, tc.arguments)

            tool_messages.append(
                Message(
                    role=MessageRole.TOOL,
                    content=json.dumps(result, ensure_ascii=False, default=str),
                    tool_call_id=tc.id,
                )
            )

        return tool_messages


async def _call_handler(handler: Callable, arguments: dict) -> Any:
    import inspect

    result = handler(**arguments)
    if inspect.isawaitable(result):
        result = await result
    return result

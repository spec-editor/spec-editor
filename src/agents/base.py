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


def _format_tool_names(tool_calls: list[ToolCall]) -> list[str]:
    """Format tool names with key arguments for trace display."""
    result = []
    for tc in tool_calls:
        name = tc.name
        args = tc.arguments or {}
        if name == "read_source_document":
            fname = args.get("filename", "")
            result.append(f"read_raw({fname})" if fname else "read_raw")
        elif name == "read_element":
            eid = args.get("element_id", "")
            result.append(f"read_element({eid})" if eid else "read_element")
        elif name == "list_aspect":
            aname = args.get("aspect_name", "")
            result.append(f"list_aspect({aname})" if aname else "list_aspect")
        elif name == "search_elements":
            q = args.get("query", "")
            result.append(f"search_elements({q[:30]})" if q else "search_elements")
        elif name == "find_related":
            eid = args.get("element_id", "")
            result.append(f"find_related({eid})" if eid else "find_related")
        elif name == "request_helper":
            role = args.get("role", "")
            result.append(f"request_helper({role})" if role else "request_helper")
        else:
            result.append(name)
    return result


def _format_tool_result(tool_name: str, result: dict) -> str:
    """Format the result of a tool call for trace display."""
    if tool_name == "run_validate":
        passed = result.get("passed", False)
        n_errors = len(result.get("errors", []))
        n_warnings = len(result.get("warnings", []))
        n_fixed = result.get("fixed", 0)
        status = "✓" if passed else "✗"
        parts = [f"{status}"]
        if n_errors:
            parts.append(f"{n_errors} errors")
        if n_warnings:
            parts.append(f"{n_warnings} warnings")
        if n_fixed:
            parts.append(f"{n_fixed} fixed")
        return (
            f"validate: {'passed' if passed else 'FAILED'} ({', '.join(parts[1:])})"
            if parts[1:]
            else f"validate: {'passed' if passed else 'FAILED'}"
        )
    if tool_name == "run_metrics":
        total = result.get("total_elements", 0)
        rels = result.get("total_relationships", 0)
        ci = result.get("connectivity_index", 0)
        orphans = result.get("orphan_elements", 0)
        return f"metrics: {total} el, {rels} rel, CI={ci:.2f}, orphans={orphans}"
    return ""


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
        trace_callback: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        """Send a message to the agent and get a response."""
        messages: list[Message] = []

        if not conversation_history:
            messages.append(
                Message(role=MessageRole.SYSTEM, content=self._system_prompt)
            )
            if trace_callback:
                trace_callback(f"[{self.name}] SYSTEM PROMPT:\n{self._system_prompt}")
        else:
            has_system = any(m.role == MessageRole.SYSTEM for m in conversation_history)
            if not has_system:
                messages.append(
                    Message(role=MessageRole.SYSTEM, content=self._system_prompt)
                )

            for msg in conversation_history:
                if msg.role == MessageRole.TOOL:
                    # Skip tool results — too many tokens. Progress is summarized
                    # in the prompt via _format_tool_summary in dialogue_manager.
                    continue
                elif msg.tool_calls:
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
        if trace_callback:
            trace_callback(f"[{self.name}] TASK:\n{user_message}")

        tools = self._tools if self._provider.supports_tools() else None
        _total_calls = 0
        _hard_limit = self._max_llm_calls * 3  # hard limit per single run() call
        _total_writes = 0  # total elements + relationships created
        _cost_per_write_limit = 0.02  # $0.02 — raised for early rounds
        _consecutive_noop = 0  # consecutive calls without productive actions
        _cumulative_prompt = 0  # accumulated prompt tokens (not reset)
        _cumulative_completion = 0  # accumulated completion tokens

        while True:
            if _total_calls >= _hard_limit:
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
            _total_calls += 1  # count only successful API calls

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
            # Format remaining tool names with arguments
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
                # Use formatted names for key tools
                pretty_other = _format_tool_names(
                    [tc for tc in response.tool_calls if tc.name in other]
                )
                parts.append(", ".join(pretty_other))
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
            display = (
                "; ".join(parts)
                if parts
                else ", ".join(_format_tool_names(response.tool_calls)[:6])
            )
            msg = f"[{self.name}] call #{_total_calls} ({total_all} tok, ~${cost:.4f}{cper}{ctotal}): {display}"
            print(msg, flush=True)

            # Show agent's reasoning text (what it plans to do and why)
            if response.content and response.content.strip():
                text = response.content.strip()[:300]
                print(f"[{self.name}]   💬 {text}", flush=True)
                if trace_callback:
                    trace_callback(f"[{self.name}]   💬 {text}")

            if trace_callback:
                trace_callback(msg)

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

            # Show run_validate / run_metrics results
            for i, tc in enumerate(response.tool_calls):
                if tc.name in ("run_validate", "run_metrics"):
                    try:
                        result = json.loads(tool_results[i].content)
                        res_msg = _format_tool_result(tc.name, result)
                        if res_msg:
                            line = f"[{self.name}]   ↳ {res_msg}"
                            print(line, flush=True)
                            if trace_callback:
                                trace_callback(line)
                    except (json.JSONDecodeError, IndexError, KeyError):
                        pass

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
            if _consecutive_noop >= 40 and _cumulative_cost > 0.05:
                msg_stop = (
                    f"[{self.name}] ⚠ Idle timeout: {_consecutive_noop} calls "
                    f"without productive output (${_cumulative_cost:.4f} spent)"
                )
                print(msg_stop, flush=True)
                if trace_callback:
                    trace_callback(msg_stop)
                return LLMResponse(
                    content=(
                        f"IDLE_TIMEOUT — {_consecutive_noop} consecutive calls "
                        f"without creating elements or relationships. "
                        f"Stopping to save costs (${_cumulative_cost:.4f} total)."
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
            self._compactor.record_tool_call(
                tc.name, tc.arguments, result if isinstance(result, dict) else None
            )

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

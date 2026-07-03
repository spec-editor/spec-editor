"""LangGraph agent — implements AgentProvider using LangGraph 1.2+.

Uses a state graph with LLM-controlled transitions:
- create_elements → run_metrics → [orphans > 0?] → link_orphans → ...
- LLM decides next step based on state
"""

import json
from typing import Any, Callable, Literal

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import TypedDict

from src.agents.provider import AgentProvider, AgentRunResult
from src.config import get_logger
from src.providers.base import LLMProvider, LLMResponse, LLMUsage, Message, ToolDef

logger = get_logger(__name__)


class AgentState(TypedDict):
    """State shared across LangGraph nodes."""

    messages: list[dict]
    total_calls: int
    total_writes: int
    consecutive_noop: int
    cumulative_cost: float
    cumulative_prompt: int
    cumulative_completion: int
    last_response: dict | None
    # Metrics tracked between calls
    last_orphans: int
    last_connectivity: float


class LangGraphAgent(AgentProvider):
    """Agent that uses LangGraph for controlled tool-calling loop.

    The graph structure:
      llm_node → [has_tool_calls?]
        → YES → tool_node → llm_node
        → NO → END

    Limits (IDLE_TIMEOUT, hard_limit) are enforced in llm_node.
    """

    def __init__(
        self,
        name: str,
        provider: LLMProvider,
        system_prompt: str,
        tools: list[ToolDef],
        tool_handlers: dict[str, Callable],
        max_llm_calls: int = 30,
        token_budget: int = 50000,
        **kwargs,  # accept SpecAgent extra params (storage, methodology, source_dir)
    ) -> None:
        super().__init__(name, provider, system_prompt, tools, tool_handlers)
        self._max_llm_calls = max_llm_calls
        self._token_budget = token_budget
        self._graph: CompiledStateGraph | None = None
        self._build_graph()

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    def _build_graph(self) -> None:
        """Build the LangGraph state graph."""
        builder = StateGraph(AgentState)

        builder.add_node("llm", self._llm_node)
        builder.add_node("tools", self._tool_node)

        builder.set_entry_point("llm")
        builder.add_conditional_edges(
            "llm",
            self._should_continue,
            {"tools": "tools", END: END},
        )
        builder.add_edge("tools", "llm")

        self._graph = builder.compile()

    async def run(
        self,
        user_message: str,
        conversation_history: list[Message] | None = None,
        trace_callback: Callable[[str], None] | None = None,
    ) -> AgentRunResult:
        """Run the agent via LangGraph."""
        # Build initial messages
        messages: list[dict] = []

        # Always add system prompt first
        messages.append({"role": "system", "content": self._system_prompt})

        if conversation_history:
            for msg in conversation_history:
                if msg.role.value == "tool":
                    # Include tool results for state context
                    messages.append(
                        {
                            "role": "tool",
                            "content": msg.content,
                            "tool_call_id": msg.tool_call_id,
                        }
                    )
                elif msg.tool_calls:
                    messages.append(
                        {
                            "role": msg.role.value,
                            "content": msg.content or "(executed tools)",
                            "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
                        }
                    )
                else:
                    messages.append(
                        {
                            "role": msg.role.value,
                            "content": msg.content or "",
                        }
                    )

        messages.append({"role": "user", "content": user_message})

        initial_state: AgentState = {
            "messages": messages,
            "total_calls": 0,
            "total_writes": 0,
            "consecutive_noop": 0,
            "cumulative_cost": 0.0,
            "cumulative_prompt": 0,
            "cumulative_completion": 0,
            "last_response": None,
            "last_orphans": -1,
            "last_connectivity": 0.0,
        }

        if trace_callback:
            trace_callback(f"[{self.name}] TASK:\n{user_message}")

        try:
            result = await self._graph.ainvoke(initial_state)
        except Exception as exc:
            logger.error("langgraph_error", agent=self.name, error=str(exc))
            return AgentRunResult(
                content=f"Error: {exc}",
            )

        last = result.get("last_response", {})
        return AgentRunResult(
            content=last.get("content", ""),
            tool_calls=last.get("tool_calls", []),
        )

    async def _llm_node(self, state: AgentState) -> AgentState:
        """LLM call node — calls provider.complete() and returns with tool_calls."""
        # Check hard limit
        hard_limit = self._max_llm_calls * 3
        if state["total_calls"] >= hard_limit:
            logger.warning(
                "llm_call_limit_reached",
                agent=self.name,
                calls=state["total_calls"],
                limit=hard_limit,
            )
            state["last_response"] = {
                "content": "(LLM call limit reached — ending turn)",
                "tool_calls": [],
            }
            return state

        # Check idle timeout
        if state["consecutive_noop"] >= 40 and state["cumulative_cost"] > 0.05:
            msg = (
                f"[{self.name}] ⚠ Idle timeout: {state['consecutive_noop']} calls "
                f"without productive output (${state['cumulative_cost']:.4f} spent)"
            )
            print(msg, flush=True)
            state["last_response"] = {
                "content": (
                    f"IDLE_TIMEOUT — {state['consecutive_noop']} consecutive calls "
                    f"without creating elements or relationships. "
                    f"Stopping to save costs (${state['cumulative_cost']:.4f} total)."
                ),
                "tool_calls": [],
            }
            return state

        # Format tools for the provider
        tools = self._tools if self._provider.supports_tools() else None

        # Convert messages to provider format
        from src.providers.base import MessageRole as MR

        provider_messages = []
        for m in state["messages"]:
            role = MR(m["role"])
            tc_list = None
            if "tool_calls" in m:
                from src.providers.base import ToolCall

                tc_list = [ToolCall(**tc) for tc in m["tool_calls"]]
            provider_messages.append(
                Message(
                    role=role,
                    content=m.get("content", ""),
                    tool_calls=tc_list,
                    tool_call_id=m.get("tool_call_id"),
                )
            )

        response = await self._provider.complete(
            messages=provider_messages, tools=tools
        )

        state["total_calls"] += 1
        state["cumulative_prompt"] += response.usage.prompt_tokens
        state["cumulative_completion"] += response.usage.completion_tokens
        state["cumulative_cost"] = (
            state["cumulative_prompt"] * 0.14 + state["cumulative_completion"] * 0.28
        ) / 1_000_000

        # Track reasoning text
        if response.content and response.content.strip():
            text = response.content.strip()[:300]
            print(f"[{self.name}]   💬 {text}", flush=True)

        # Count writes
        batch_writes = 0
        if response.tool_calls:
            batch_writes = sum(
                1
                for tc in response.tool_calls
                if tc.name
                in ("write_element", "add_relationship", "remove_relationship")
            )

        state["total_writes"] += batch_writes
        if batch_writes > 0:
            state["consecutive_noop"] = 0
        else:
            state["consecutive_noop"] += 1

        # Check cost threshold
        if state["total_writes"] > 0:
            cost_per_write = state["cumulative_cost"] / state["total_writes"]
            if state["total_calls"] >= 5 and cost_per_write > 0.02:
                print(
                    f"[{self.name}] ⚠ STOP: cost ${state['cumulative_cost']:.4f} / "
                    f"{state['total_writes']} writes = ${cost_per_write:.4f}/write",
                    flush=True,
                )
                state["last_response"] = {
                    "content": f"(: ${cost_per_write:.4f}/write > $0.02)",
                    "tool_calls": [],
                }
                return state

        # Add assistant message
        assistant_msg: dict = {
            "role": "assistant",
            "content": response.content or "",
        }
        if response.tool_calls:
            assistant_msg["tool_calls"] = [
                tc.model_dump() for tc in response.tool_calls
            ]

        state["messages"].append(assistant_msg)
        state["last_response"] = assistant_msg

        # Show call summary
        cost = (
            response.usage.prompt_tokens * 0.14
            + response.usage.completion_tokens * 0.28
        ) / 1_000_000
        cper = (
            f", ${state['cumulative_cost'] / max(state['total_writes'], 1):.4f}/write"
            if state["total_writes"]
            else ""
        )
        ctotal = (
            f", total ${state['cumulative_cost']:.4f}"
            if state["cumulative_cost"] > 0.01
            else ""
        )
        tool_names = [tc.name for tc in (response.tool_calls or [])]
        n_writes = sum(1 for n in tool_names if n == "write_element")
        n_rels = sum(
            1 for n in tool_names if n in ("add_relationship", "remove_relationship")
        )
        n_reads = sum(
            1
            for n in tool_names
            if n in ("read_element", "list_aspect", "search_elements")
        )
        parts = []
        if n_writes:
            parts.append(f"+{n_writes} elements")
        if n_rels:
            parts.append(f"{n_rels} relationships")
        if n_reads and not n_writes and not n_rels:
            parts.append(f"reads {n_reads} elements")
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
            )
        ]
        if other:
            parts.append(", ".join(other))
        display = (
            "; ".join(parts)
            if parts
            else (", ".join(tool_names[:6]) if tool_names else "response")
        )

        total_all = response.usage.prompt_tokens + response.usage.completion_tokens
        msg = (
            f"[{self.name}] call #{state['total_calls']} "
            f"({total_all} tok, ~${cost:.4f}{cper}{ctotal}): {display}"
        )
        print(msg, flush=True)

        return state

    async def _tool_node(self, state: AgentState) -> AgentState:
        """Execute tool calls from last assistant message."""
        last = state["last_response"]
        if not last or "tool_calls" not in last:
            return state

        from src.providers.base import ToolCall

        tool_calls = [ToolCall(**tc) for tc in last["tool_calls"]]
        tool_results = await self._execute_tools(tool_calls, state)

        state["messages"].extend(tool_results)

        # Check call limit
        if state["total_calls"] >= self._max_llm_calls * 3:
            state["last_response"] = {
                "content": "(LLM call limit reached — ending turn)",
                "tool_calls": [],
            }
        elif state["consecutive_noop"] >= 40 and state["cumulative_cost"] > 0.05:
            msg = (
                f"[{self.name}] ⚠ Idle timeout: {state['consecutive_noop']} calls "
                f"without productive output (${state['cumulative_cost']:.4f} spent)"
            )
            print(msg, flush=True)
            state["last_response"] = {
                "content": (
                    f"IDLE_TIMEOUT — {state['consecutive_noop']} consecutive calls "
                    f"without creating elements or relationships. "
                    f"Stopping to save costs (${state['cumulative_cost']:.4f} total)."
                ),
                "tool_calls": [],
            }

        return state

    async def _execute_tools(self, tool_calls: list, state: AgentState) -> list[dict]:
        """Execute tool calls and return TOOL messages for state."""
        from src.agents.base import _call_handler, _format_tool_result

        tool_messages = []
        for tc in tool_calls:
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

            # Show metrics results
            if tc.name in ("run_validate", "run_metrics"):
                try:
                    res_msg = _format_tool_result(tc.name, result)
                    if res_msg:
                        line = f"[{self.name}]   ↳ {res_msg}"
                        print(line, flush=True)
                        # Track orphans from metrics
                        if tc.name == "run_metrics":
                            state["last_orphans"] = result.get("orphan_elements", -1)
                            state["last_connectivity"] = result.get(
                                "connectivity_index", 0.0
                            )
                except Exception:
                    pass

            tool_messages.append(
                {
                    "role": "tool",
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                    "tool_call_id": tc.id,
                }
            )

        return tool_messages

    @staticmethod
    def _should_continue(state: AgentState) -> Literal["tools", "__end__"]:
        """Decide whether to continue to tools or end."""
        last = state.get("last_response", {})
        if last and last.get("tool_calls"):
            return "tools"
        return END

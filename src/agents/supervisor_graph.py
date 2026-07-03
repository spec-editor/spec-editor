"""Multi-agent LangGraph graph — supervisor + PARALLEL agents with shared state.

Replaces DialogueManager + MessageBus for LangGraph mode.
Architecture:
  supervisor → runs agent_1 and agent_2 in parallel via asyncio.gather
  Each agent does a full LLM+tools loop within its task
  Supervisor waits for both, then reassesses metrics and continues/ends

Key: agents run in PARALLEL within each round via asyncio.gather,
sharing the same storage (thread-safe via filesystem writes).
"""

import asyncio
import json
import operator
import time
from pathlib import Path
from typing import Annotated, Any, Callable, Literal

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import TypedDict

from src.agents.provider import AgentProvider, AgentRunResult
from src.config import get_logger
from src.tracing import implements
from src.config.settings import AgentsConfig
from src.mcp.metrics import MetricsReport, compute_metrics
from src.providers.base import (
    LLMProvider,
    LLMResponse,
    LLMUsage,
    Message,
    MessageRole,
    ToolCall,
    ToolDef,
)
from src.storage.adapter import StorageAdapter

logger = get_logger(__name__)


class TeamState(TypedDict):
    """State shared across all agents in the supervisor graph."""

    messages: Annotated[list[dict], operator.add]
    # Team-level budget tracking (shared across all agents)
    total_calls: int  # cumulative LLM calls across whole team
    total_writes: int  # cumulative writes (elements + relationships)
    total_cost: float  # cumulative cost across whole team
    # Per-agent counters (for logging only, not budgeting)
    agent1_calls: int
    agent2_calls: int
    agent1_cost: float
    agent2_cost: float
    # Metrics tracked across the team
    last_metrics: dict | None
    prev_metrics: dict | None  # metrics from previous round for staleness detection
    stale_count: int  # consecutive rounds without metric improvement
    # Control
    round_num: int
    max_rounds: int
    start_time: float
    max_time_seconds: float
    status: str  # "running", "complete", "timeout", "stalled"
    last_activity: float
    # Agent-specific task messages
    agent1_task: str
    agent2_task: str


@implements("CA-003")
class SupervisorGraph:
    """Builds and runs the multi-agent LangGraph supervisor with PARALLEL agents.

    Usage:
        graph = SupervisorGraph(storage, config, provider_factory, ...)
        result = await graph.run(initial_task)
    """

    def __init__(
        self,
        storage: StorageAdapter,
        config: AgentsConfig,
        provider_factory: Callable[[str], LLMProvider],
        agent1_prompt: str,
        agent2_prompt: str,
        agent1_tools: list[ToolDef],
        agent2_tools: list[ToolDef],
        agent1_handlers: dict[str, Callable],
        agent2_handlers: dict[str, Callable],
        max_llm_calls: int = 30,
        log_dir: Path | None = None,
        project_path: Path | None = None,
        source_dir: str = "",
        ci_threshold: float | None = None,
    ) -> None:
        self._storage = storage
        self._config = config
        self._provider_factory = provider_factory
        self._agent1_prompt = agent1_prompt
        self._agent2_prompt = agent2_prompt
        self._agent1_tools = agent1_tools
        self._agent2_tools = agent2_tools
        self._agent1_handlers = agent1_handlers
        self._agent2_handlers = agent2_handlers
        self._user_ci_threshold = ci_threshold
        self._max_llm_calls = max_llm_calls
        self._log_dir = log_dir
        self._project_path = project_path
        self._source_dir = source_dir
        self._graph: CompiledStateGraph | None = None
        self._helper_counter = 0
        self._helper_tasks: list[asyncio.Task] = []
        self._build_graph()

    def _build_graph(self) -> None:
        """Build the graph.

        Flow:
          supervisor → runs agents in parallel (inside the node) → self-loop
          supervisor → finalize → END

        The supervisor node handles everything: metric computation,
        parallel agent dispatch, and completion decisions.
        """
        builder = StateGraph(TeamState)

        builder.add_node("supervisor", self._supervisor_node)
        builder.add_node("finalize", self._finalize_node)

        builder.set_entry_point("supervisor")

        # Supervisor either loops back to itself or goes to finalize
        builder.add_conditional_edges(
            "supervisor",
            self._route_supervisor,
            {"supervisor": "supervisor", "finalize": "finalize"},
        )

        builder.add_edge("finalize", END)

        self._graph = builder.compile()

    async def run(self, initial_task: str, resume: bool = False) -> dict:
        """Run the multi-agent team.

        Args:
            initial_task: Task for agents.
            resume: If True, restore from checkpoint and continue.
                    If False (default), start fresh.
        """
        start_time = time.monotonic()

        # Try to restore from checkpoint
        checkpoint = self._load_checkpoint() if resume else None
        if checkpoint:
            initial_messages = checkpoint.get("messages", [])
            logger.info(
                "checkpoint_restored",
                round=checkpoint.get("round_num", 0),
                calls_a1=checkpoint.get("agent1_calls", 0),
                calls_a2=checkpoint.get("agent2_calls", 0),
                elements=checkpoint.get("last_metrics", {}).get("total_elements", "?"),
            )
            print(
                f"[RESUME] Restored checkpoint: "
                f"round {checkpoint.get('round_num', 0)}, "
                f"{checkpoint.get('last_metrics', {}).get('total_elements', '?')} elements, "
                f"${checkpoint.get('total_cost', 0):.4f} spent",
                flush=True,
            )
        else:
            initial_messages = [
                {"role": "user", "content": initial_task},
            ]

        initial_state: TeamState = {
            "messages": initial_messages,
            "total_calls": checkpoint.get("total_calls", 0) if checkpoint else 0,
            "total_writes": checkpoint.get("total_writes", 0) if checkpoint else 0,
            "total_cost": checkpoint.get("total_cost", 0.0) if checkpoint else 0.0,
            "agent1_calls": checkpoint.get("agent1_calls", 0) if checkpoint else 0,
            "agent2_calls": checkpoint.get("agent2_calls", 0) if checkpoint else 0,
            "agent1_cost": checkpoint.get("agent1_cost", 0.0) if checkpoint else 0.0,
            "agent2_cost": checkpoint.get("agent2_cost", 0.0) if checkpoint else 0.0,
            "last_metrics": checkpoint.get("last_metrics") if checkpoint else None,
            "prev_metrics": checkpoint.get("prev_metrics") if checkpoint else None,
            "stale_count": checkpoint.get("stale_count", 0) if checkpoint else 0,
            "round_num": checkpoint.get("round_num", 0) if checkpoint else 0,
            "max_rounds": 8,
            "start_time": start_time,
            "max_time_seconds": self._config.max_time_minutes * 60,
            "status": "running",
            "last_activity": start_time,
            "agent1_task": "",
            "agent2_task": "",
        }

        # Only delete checkpoint on fresh start — resume keeps it for crash recovery
        if not resume:
            self._delete_checkpoint()

        try:
            result = await self._graph.ainvoke(initial_state)
        except Exception as exc:
            logger.error("supervisor_graph_error", error=str(exc))
            # Save checkpoint on crash for recovery
            self._save_checkpoint(initial_state)
            result = {"status": "error", "error": str(exc)}

        # Clean up checkpoint on successful completion (both fresh and resume)
        self._delete_checkpoint()

        return result

    # ═══════════════════════════════════════════════════════════════
    # Supervisor Node
    # ═══════════════════════════════════════════════════════════════

    async def _supervisor_node(self, state: TeamState) -> dict:
        """Supervisor: compute metrics, decide which agents to run,
        run them IN PARALLEL via asyncio.gather, then return updated state.

        Budget model:
        - Hard limit: total_calls >= max_llm_calls * 3  → stop
        - Cost efficiency: cost_per_write > $0.02 and total_writes >= 5 → stop
        - Round limit: round_num >= max_rounds → stop if orphans==0 or CI>=0.7
        - Idle: agents self-stop after 15 no-op calls (inside _agent_loop)
        - report_complete + orphans==0 → stop
        """
        now = time.monotonic()

        # Timeout check
        if now - state["start_time"] > state["max_time_seconds"]:
            return {"status": "timeout"}

        # Compute metrics
        try:
            m = compute_metrics(self._storage)
            state["last_metrics"] = m.model_dump()
        except Exception:
            pass

        metrics = state.get("last_metrics", {})
        orphans = metrics.get("orphan_elements", 999)
        connectivity = metrics.get("connectivity_index", 0)
        unparented = metrics.get("unparented_elements", 0)
        total = metrics.get("total_elements", 0)
        non_src = total - metrics.get("aspects", {}).get("sources", 0)

        # ── Budget checks (team-level, not per-agent) ──

        total_calls = state.get("total_calls", 0)
        total_writes = state.get("total_writes", 0)
        total_cost = state.get("total_cost", 0.0)

        # Soft limit: stop if no metric improvement after N consecutive checks
        STALE_LIMIT = 3  # consecutive rounds without improvement → stalled
        prev_metrics = state.get("prev_metrics")
        stale_count = state.get("stale_count", 0)

        # Compare current metrics with previous to detect improvement
        improved = False
        if prev_metrics:
            prev_elements = prev_metrics.get("total_elements", 0)
            prev_relationships = prev_metrics.get("total_relationships", 0)
            prev_ci = prev_metrics.get("connectivity_index", 0)
            prev_orphans = prev_metrics.get("orphan_elements", 999)

            curr_elements = metrics.get("total_elements", 0)
            curr_relationships = metrics.get("total_relationships", 0)
            curr_ci = metrics.get("connectivity_index", 0)
            curr_orphans = metrics.get("orphan_elements", 999)

            improved = (
                curr_elements > prev_elements
                or curr_relationships > prev_relationships
                or curr_ci > prev_ci
                or (curr_orphans < prev_orphans and curr_orphans < 999)
            )

        if improved:
            stale_count = 0
            logger.info("supervisor_metrics_improved", reset_stale=True)
        elif total_calls > 0:  # Don't count the initial state
            stale_count += 1

        if stale_count >= STALE_LIMIT:
            logger.info(
                "supervisor_stale_limit",
                stale_count=stale_count,
                limit=STALE_LIMIT,
                ci=connectivity,
                orphans=orphans,
            )
            return {
                "status": "stalled",
                "stale_count": stale_count,
            }

        # Save current metrics as prev for next round
        state["prev_metrics"] = dict(metrics)
        state["stale_count"] = stale_count

        # Cost efficiency: stop if each write is too expensive
        cost_per_write = total_cost / max(total_writes, 1)
        COST_THRESHOLD = 0.02  # $0.02 per write
        if total_writes >= 5 and cost_per_write > COST_THRESHOLD:
            logger.info(
                "supervisor_cost_threshold",
                cost_per_write=f"${cost_per_write:.4f}",
                total_writes=total_writes,
                total_cost=f"${total_cost:.4f}",
            )
            return {"status": "complete"}

        # ── Quality checks ──

        # Stalled: no activity for 60s and no progress
        if now - state["last_activity"] > 60 and non_src > 0:
            logger.info("supervisor_stalled")
            return {"status": "stalled"}

        # Round limit: stop if quality is sufficient
        if state["round_num"] >= state["max_rounds"]:
            round_ci_min = self._user_ci_threshold if self._user_ci_threshold else 0.7
            if (orphans == 0 and unparented == 0) or connectivity >= round_ci_min:
                return {"status": "complete"}
            # Continue but log warning
            logger.info(
                "supervisor_round_limit_reached",
                round=state["round_num"],
                orphans=orphans,
                ci=connectivity,
            )

        # Agent declared completion — verify with metrics
        if self._has_report_complete(state):
            import math

            # Use user-provided threshold, or compute dynamically
            if self._user_ci_threshold is not None:
                ci_threshold = self._user_ci_threshold
            else:
                num_elements = max(metrics.get("total_elements", 0), 10)
                ci_threshold = round(2.0 + math.log(max(num_elements, 10)) * 0.35, 2)
                ci_threshold = max(2.0, min(ci_threshold, 3.5))

            # Accept completion if: CI meets threshold, OR agents stuck without improvement
            ci_ok = connectivity >= ci_threshold
            stuck = stale_count >= STALE_LIMIT and orphans == 0 and unparented == 0

            if orphans == 0 and unparented == 0 and (ci_ok or stuck):
                logger.info(
                    "supervisor_report_complete_verified",
                    ci=connectivity,
                    threshold=ci_threshold,
                    unparented=unparented,
                    stale_count=stale_count,
                    stuck=stuck,
                )
                return {"status": "complete"}
            else:
                logger.info(
                    "supervisor_report_complete_rejected",
                    orphans=orphans,
                    unparented=unparented,
                    ci=connectivity,
                    threshold=ci_threshold,
                    stale_count=stale_count,
                )

        # ── Decide which agents to activate ──

        agent1_active = False
        agent2_active = False

        metrics_aspects = metrics.get("aspects", {})
        missing_aspects = self._get_missing_aspects(metrics_aspects)

        if missing_aspects or connectivity < 0.5:
            agent1_active = True
        if orphans > 0 and non_src > 0:
            agent2_active = True

        # If nothing specific, let both work
        if not agent1_active and not agent2_active:
            agent1_active = True
            agent2_active = True

        # Don't activate agents with no prompt/tools (disabled).
        if not self._agent2_prompt or not self._agent2_tools:
            agent2_active = False

        # ── Prepare and run ──

        new_round = state["round_num"] + 1

        agent1_task = self._build_context(metrics, "agent_1") if agent1_active else ""
        agent2_task = self._build_context(metrics, "agent_2") if agent2_active else ""

        print(
            f"\n[SUPERVISOR] Round {new_round}: "
            f"{'Agent 1' if agent1_active else ''}"
            f"{' + ' if agent1_active and agent2_active else ''}"
            f"{'Agent 2' if agent2_active else ''}"
            f" | {total} el, {metrics.get('total_relationships', 0)} rel, "
            f"CI={connectivity:.2f}, orphans={orphans}"
            f" | ${cost_per_write:.4f}/write"
            f" | ${total_cost:.4f} total, {total_calls} calls",
            flush=True,
        )

        # ── Run agents IN PARALLEL with shared message bus ──
        shared_messages: list[dict] = []

        # Inject request_helper spawner
        async def _spawner(role: str, task: str) -> dict:
            return await self._spawn_helper(role, task, shared_messages)

        a1_handlers = dict(self._agent1_handlers)
        a1_handlers["request_helper"] = _spawner
        a2_handlers = dict(self._agent2_handlers)
        a2_handlers["request_helper"] = _spawner

        # Per-round budget: soft cap prevents single round from going wild
        per_agent_budget = max(10, self._max_llm_calls // (2 if agent1_active and agent2_active else 1))

        tasks = []
        if agent1_active:
            tasks.append(
                self._agent_loop(
                    state=state,
                    agent_name="Agent 1",
                    system_prompt=self._agent1_prompt,
                    tools=self._agent1_tools,
                    handlers=a1_handlers,
                    call_count_key="agent1_calls",
                    cost_key="agent1_cost",
                    task=agent1_task,
                    shared_messages=shared_messages,
                    max_calls=per_agent_budget,
                )
            )
        if agent2_active:
            tasks.append(
                self._agent_loop(
                    state=state,
                    agent_name="Agent 2",
                    system_prompt=self._agent2_prompt,
                    tools=self._agent2_tools,
                    handlers=a2_handlers,
                    call_count_key="agent2_calls",
                    cost_key="agent2_cost",
                    task=agent2_task,
                    shared_messages=shared_messages,
                    max_calls=per_agent_budget,
                )
            )

        # Wait for all agents in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Aggregate results — accumulate team-level counters.
        # Each agent returns absolute team totals (snapshot + its delta).
        # We take the last non-exception result as the authoritative state.
        team_deltas = {"total_calls": 0, "total_writes": 0, "total_cost": 0.0}
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error("agent_loop_error", agent=i + 1, error=str(result))
                continue
            if isinstance(result, dict):
                # Track per-agent deltas for accurate logging
                a1_delta = result.get("_a1_calls_delta", 0)
                a2_delta = result.get("_a2_calls_delta", 0)
                a1_cost_delta = result.get("_a1_cost_delta", 0.0)
                a2_cost_delta = result.get("_a2_cost_delta", 0.0)

                state["agent1_calls"] = state.get("agent1_calls", 0) + a1_delta
                state["agent2_calls"] = state.get("agent2_calls", 0) + a2_delta
                state["agent1_cost"] = state.get("agent1_cost", 0.0) + a1_cost_delta
                state["agent2_cost"] = state.get("agent2_cost", 0.0) + a2_cost_delta

                # Team totals: accumulate deltas from all agents
                for key in ("total_calls", "total_writes", "total_cost"):
                    if key in result:
                        team_deltas[key] = max(
                            team_deltas[key],
                            result[key] - state.get(key, 0),
                        )

        # Apply accumulated team deltas
        for key in ("total_calls", "total_writes", "total_cost"):
            state[key] = state.get(key, 0) + team_deltas[key]

        # Save checkpoint after round for crash recovery
        self._save_checkpoint(state)

        return {
            "round_num": new_round,
            "last_activity": time.monotonic(),
            "messages": shared_messages,
            "total_calls": state.get("total_calls", 0),
            "total_writes": state.get("total_writes", 0),
            "total_cost": state.get("total_cost", 0.0),
            "agent1_calls": state.get("agent1_calls", 0),
            "agent2_calls": state.get("agent2_calls", 0),
            "agent1_cost": state.get("agent1_cost", 0.0),
            "agent2_cost": state.get("agent2_cost", 0.0),
            "stale_count": state.get("stale_count", 0),
            "prev_metrics": state.get("prev_metrics"),
            "status": "running",
        }

    # ═══════════════════════════════════════════════════════════════
    # Agent Loop (runs inside supervisor node, called via asyncio.gather)
    # ═══════════════════════════════════════════════════════════════

    async def _agent_loop(
        self,
        state: TeamState,
        agent_name: str,
        system_prompt: str,
        tools: list[ToolDef],
        handlers: dict[str, Callable],
        call_count_key: str,
        cost_key: str,
        task: str,
        shared_messages: list[dict],
        max_calls: int = 10,
    ) -> dict:
        """Run a full LLM+tools loop for one agent.

        Returns a dict with updated counters.
        Messages are written directly to shared_messages so the sibling
        agent sees them in real-time.
        """
        from src.agents.base import _call_handler, _format_tool_result

        provider = self._provider_factory(
            "agent_1" if agent_name == "Agent 1" else "agent_2"
        )

        max_calls_this_round = max_calls
        calls_this_round = 0
        consecutive_noop = 0
        round_writes = 0
        local_calls = state[call_count_key]
        local_cost = state[cost_key]

        # Add task message to shared bus immediately so sibling sees it
        if task:
            shared_messages.append({"role": "user", "content": task})

        while calls_this_round < max_calls_this_round:
            # Check team-level hard limit (shared budget)
            hard_limit = self._max_llm_calls * 3
            if state.get("total_calls", 0) + calls_this_round >= hard_limit:
                break

            # Build messages for LLM:
            # - system prompt
            # - state["messages"] (from previous rounds, frozen snapshot)
            # - shared_messages (real-time updates from BOTH agents this round)
            provider_messages = [
                Message(role=MessageRole.SYSTEM, content=system_prompt)
            ]

            # Previous rounds' messages
            for m in state["messages"]:
                role = MessageRole(m["role"])
                tc_list = None
                if "tool_calls" in m:
                    tc_list = [ToolCall(**tc) for tc in m["tool_calls"]]
                provider_messages.append(
                    Message(
                        role=role,
                        content=m.get("content", ""),
                        tool_calls=tc_list,
                        tool_call_id=m.get("tool_call_id"),
                    )
                )

            # Real-time shared messages from BOTH agents this round
            # Take a snapshot to avoid race conditions during iteration
            snapshot = list(shared_messages)
            for m in snapshot:
                role = MessageRole(m["role"])
                tc_list = None
                if "tool_calls" in m:
                    tc_list = [ToolCall(**tc) for tc in m["tool_calls"]]
                provider_messages.append(
                    Message(
                        role=role,
                        content=m.get("content", ""),
                        tool_calls=tc_list,
                        tool_call_id=m.get("tool_call_id"),
                    )
                )

            tools_for_llm = tools if provider.supports_tools() else None

            try:
                response = await provider.complete(
                    messages=provider_messages, tools=tools_for_llm
                )
            except Exception as exc:
                logger.error(f"{agent_name}_llm_error", error=str(exc))
                shared_messages.append(
                    {"role": "assistant", "content": f"Error: {exc}"}
                )
                break

            # Update counters
            local_calls += 1
            calls_this_round += 1
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens
            cost = (prompt_tokens * 0.14 + completion_tokens * 0.28) / 1_000_000
            local_cost += cost

            # Reasoning
            if response.content and response.content.strip():
                text = response.content.strip()[:200]
                print(f"[{agent_name.upper()}] 💬 {text}", flush=True)

            # Build assistant message
            assistant_msg: dict = {
                "role": "assistant",
                "content": response.content or "",
            }
            if response.tool_calls:
                assistant_msg["tool_calls"] = [
                    tc.model_dump() for tc in response.tool_calls
                ]

            # Write to shared bus — sibling agent will see this on its next LLM call
            shared_messages.append(assistant_msg)

            # If no tool calls — agent is done for this round
            if not response.tool_calls:
                break

            # Execute tools
            batch_writes = 0
            for tc_data in assistant_msg["tool_calls"]:
                tc = ToolCall(**tc_data)
                handler = handlers.get(tc.name)
                if handler is None:
                    result = {"error": f"Unknown tool: {tc.name}"}
                else:
                    try:
                        result = await _call_handler(handler, tc.arguments)
                    except Exception as exc:
                        result = {"error": str(exc)}
                        logger.error(
                            "tool_error",
                            agent=agent_name,
                            tool=tc.name,
                            error=str(exc),
                        )

                if tc.name in (
                    "write_element",
                    "add_relationship",
                    "remove_relationship",
                ):
                    batch_writes += 1

                if tc.name in ("run_validate", "run_metrics"):
                    try:
                        res_msg = _format_tool_result(tc.name, result)
                        if res_msg:
                            print(
                                f"[{agent_name.upper()}]   ↳ {res_msg}",
                                flush=True,
                            )
                    except Exception:
                        pass

                shared_messages.append(
                    {
                        "role": "tool",
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                        "tool_call_id": tc.id,
                    }
                )

            round_writes += batch_writes

            if batch_writes > 0:
                consecutive_noop = 0
            else:
                consecutive_noop += 1

            # Auto-inject metrics snapshot every 5 calls so agents see progress
            # without having to explicitly call run_metrics
            if calls_this_round > 0 and calls_this_round % 5 == 0:
                try:
                    from src.mcp.metrics import compute_metrics

                    ms = compute_metrics(self._storage)
                    summary = (
                        f"[AUTO-METRICS] {ms.total_elements} elements, "
                        f"{ms.total_relationships} relationships, "
                        f"CI={ms.connectivity_index:.2f}, "
                        f"orphans={ms.orphan_elements}"
                    )
                    shared_messages.append({"role": "user", "content": summary})
                except Exception:
                    pass

            if consecutive_noop >= 15 and local_cost > 0.02:
                print(
                    f"[{agent_name.upper()}] ⚠ Idle — {consecutive_noop} calls "
                    f"without writes, yielding round",
                    flush=True,
                )
                break

            # Log
            tool_names = [tc.name for tc in (response.tool_calls or [])]
            n_writes = sum(1 for n in tool_names if n == "write_element")
            n_rels = sum(
                1
                for n in tool_names
                if n in ("add_relationship", "remove_relationship")
            )
            n_reads = sum(1 for n in tool_names if n == "read_element")
            parts = []
            if n_writes:
                parts.append(f"+{n_writes} elements")
            if n_rels:
                parts.append(f"{n_rels} relationships")
            if n_reads and not n_writes:
                parts.append(f"reads {n_reads} elements")
            display = (
                "; ".join(parts)
                if parts
                else (", ".join(tool_names[:4]) if tool_names else "response")
            )

            total_tok = prompt_tokens + completion_tokens
            print(
                f"[{agent_name.upper()}] call #{local_calls} "
                f"({total_tok} tok, ~${cost:.4f}, total ${local_cost:.4f})"
                f": {display}",
                flush=True,
            )

        # Return team-level aggregates: current counters + this round's delta
        is_agent1 = call_count_key == "agent1_calls"
        return {
            "total_calls": state.get("total_calls", 0) + calls_this_round,
            "total_writes": state.get("total_writes", 0) + round_writes,
            "total_cost": state.get("total_cost", 0.0) + local_cost,
            "_a1_calls_delta": calls_this_round if is_agent1 else 0,
            "_a2_calls_delta": 0 if is_agent1 else calls_this_round,
            "_a1_cost_delta": local_cost if is_agent1 else 0.0,
            "_a2_cost_delta": 0.0 if is_agent1 else local_cost,
        }

    # ═══════════════════════════════════════════════════════════════
    # Helper Agents (sub-agents spawned via request_helper)
    # ═══════════════════════════════════════════════════════════════

    async def _spawn_helper(
        self, role: str, task: str, shared_messages: list[dict]
    ) -> dict:
        """Spawn a helper agent that runs in parallel with main agents.

        The helper gets a specialized prompt from skills.yaml (if available)
        and its own tool set. Results are written to shared_messages so
        main agents see them in real-time.

        Called as the spawner function for request_helper tool.
        """
        # Clean up completed helper tasks
        self._helper_tasks = [t for t in self._helper_tasks if not t.done()]
        if len(self._helper_tasks) >= 8:
            return {
                "status": "error",
                "message": "Helper limit reached (8). Wait for existing helpers.",
            }

        self._helper_counter += 1
        helper_name = f"Helper-{role}-{self._helper_counter}"

        # Get skill prompt if available
        skill_name = None  # track which skill was loaded (for logging)
        skill_prompt = ""
        skill_tool_names = None
        if self._project_path:
            # Prefer skills/ directory; also load skills.yaml from root
            # if it still exists (legacy compat).
            skills_dir = self._project_path / "skills"
            file_path = self._project_path / "skills.yaml"
            if skills_dir.is_dir():
                skills_paths: list[Path] = [skills_dir]
                if file_path.exists():
                    skills_paths.append(file_path)
            elif file_path.exists():
                skills_paths = [file_path]
            else:
                skills_paths = []

            if skills_paths:
                from src.config.skills import SkillsRegistry

                registry = SkillsRegistry(skills_paths)
                skill = registry.get(role)

                # Fuzzy match if exact match fails (agents use shorthand roles)
                if skill is None:
                    role_lower = role.lower()
                    for skill_obj in registry._skills.values():
                        sk_lower = skill_obj.name.lower()
                        # Match: "scenarios" ↔ "scenario_decomposer"
                        # Match: "UI" ↔ "ui_navigator"
                        # Match: "metrics" ↔ "metrics_linker"
                        if role_lower in sk_lower or sk_lower in role_lower:
                            skill = skill_obj
                            break

                if skill:
                    skill_name = skill.name
                    skill_prompt = skill.prompt
                    skill_tool_names = set(skill.tools) if skill.tools else None
                    logger.info(
                        "helper_skill_loaded",
                        helper=helper_name,
                        skill=skill_name,
                        tools_count=len(skill_tool_names) if skill_tool_names else 0,
                    )
                    print(
                        f"[HELPER:{helper_name}] Skill: '{skill_name}' prompt={len(skill_prompt)} chars, "
                        f"tools={sorted(skill_tool_names) if skill_tool_names else 'all'}",
                        flush=True,
                    )
                else:
                    logger.info(
                        "helper_skill_not_found",
                        helper=helper_name,
                        role=role,
                    )
                    print(
                        f"[HELPER:{helper_name}] Skill NOT FOUND for role '{role}' — using generic prompt",
                        flush=True,
                    )

        # Use Agent 1's tools as base, but filter if skill specifies tools
        helper_tools = list(self._agent1_tools)
        helper_handlers = dict(self._agent1_handlers)
        if skill_tool_names:
            helper_tools = [t for t in helper_tools if t.name in skill_tool_names]
            helper_handlers = {
                k: v for k, v in helper_handlers.items() if k in skill_tool_names
            }

        # Build helper prompt: skill prompt or truncated agent1 prompt
        if skill_prompt:
            helper_prompt = skill_prompt
        else:
            # Use agent1 prompt but add role-specific header
            helper_prompt = (
                f"Your role: {role}. Task: {task}.\n"
                f"Read the dialogue history and join the work.\n"
                f"After completing your task, report_complete.\n\n"
                + self._agent1_prompt
            )

        # Create the helper loop task
        helper_task = asyncio.create_task(
            self._helper_loop(
                helper_name=helper_name,
                system_prompt=helper_prompt,
                tools=helper_tools,
                handlers=helper_handlers,
                task=task,
                shared_messages=shared_messages,
            )
        )
        self._helper_tasks.append(helper_task)

        logger.info(
            "helper_spawned",
            name=helper_name,
            role=role,
            skill=skill_name,
        )
        return {
            "status": "ok",
            "helper": helper_name,
            "role": role,
            "task": task,
        }

    async def _helper_loop(
        self,
        helper_name: str,
        system_prompt: str,
        tools: list[ToolDef],
        handlers: dict[str, Callable],
        task: str,
        shared_messages: list[dict],
    ) -> dict:
        """Run a helper agent's LLM+tools loop.

        Helper gets the same budget as main agents so it can do meaningful work.
        It sees shared_messages and writes results back after completion.
        """
        from src.agents.base import _call_handler, _format_tool_result

        provider = self._provider_factory("agent_1")
        # Helpers have tighter budget: max 15 calls (half of main agent)
        # They should do focused work, not full exploration
        max_calls = min(max(5, self._max_llm_calls // 2), 15)
        calls = 0
        cost = 0.0
        writes = 0
        local_messages: list[dict] = []

        # Add task
        local_messages.append(
            {"role": "user", "content": f"[{helper_name}] Task: {task}"}
        )

        print(f"[HELPER:{helper_name}] Started — {task[:100]}", flush=True)

        while calls < max_calls:
            # Build messages: system + shared state + local
            provider_messages = [
                Message(role=MessageRole.SYSTEM, content=system_prompt)
            ]

            # Include shared_messages (what main agents have done)
            snapshot = list(shared_messages)
            for m in snapshot:
                role = MessageRole(m["role"])
                tc_list = None
                if "tool_calls" in m:
                    tc_list = [ToolCall(**tc) for tc in m["tool_calls"]]
                provider_messages.append(
                    Message(
                        role=role,
                        content=m.get("content", ""),
                        tool_calls=tc_list,
                        tool_call_id=m.get("tool_call_id"),
                    )
                )

            # Include local messages
            for m in local_messages:
                role = MessageRole(m["role"])
                tc_list = None
                if "tool_calls" in m:
                    tc_list = [ToolCall(**tc) for tc in m["tool_calls"]]
                provider_messages.append(
                    Message(
                        role=role,
                        content=m.get("content", ""),
                        tool_calls=tc_list,
                        tool_call_id=m.get("tool_call_id"),
                    )
                )

            tools_for_llm = tools if provider.supports_tools() else None

            try:
                response = await provider.complete(
                    messages=provider_messages, tools=tools_for_llm
                )
            except Exception as exc:
                logger.error(f"{helper_name}_error", error=str(exc))
                local_messages.append({"role": "assistant", "content": f"Error: {exc}"})
                break

            calls += 1
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens
            call_cost = (prompt_tokens * 0.14 + completion_tokens * 0.28) / 1_000_000
            cost += call_cost

            # Reasoning
            if response.content and response.content.strip():
                text = response.content.strip()[:150]
                print(f"[HELPER:{helper_name}] 💬 {text}", flush=True)

            # Build assistant message
            assistant_msg: dict = {
                "role": "assistant",
                "content": response.content or "",
            }
            if response.tool_calls:
                assistant_msg["tool_calls"] = [
                    tc.model_dump() for tc in response.tool_calls
                ]

            local_messages.append(assistant_msg)

            if not response.tool_calls:
                break

            # Execute tools
            batch_writes = 0
            for tc_data in assistant_msg["tool_calls"]:
                tc = ToolCall(**tc_data)
                handler = handlers.get(tc.name)
                if handler is None:
                    result = {"error": f"Unknown tool: {tc.name}"}
                else:
                    try:
                        result = await _call_handler(handler, tc.arguments)
                    except Exception as exc:
                        result = {"error": str(exc)}

                if tc.name in (
                    "write_element",
                    "add_relationship",
                    "remove_relationship",
                ):
                    batch_writes += 1

                if tc.name in ("run_validate", "run_metrics"):
                    try:
                        res_msg = _format_tool_result(tc.name, result)
                        if res_msg:
                            print(
                                f"[HELPER:{helper_name}]   ↳ {res_msg}",
                                flush=True,
                            )
                    except Exception:
                        pass

                local_messages.append(
                    {
                        "role": "tool",
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                        "tool_call_id": tc.id,
                    }
                )

            writes += batch_writes

            # Log
            tool_names = [tc.name for tc in (response.tool_calls or [])]
            n_writes = sum(1 for n in tool_names if n == "write_element")
            n_rels = sum(
                1
                for n in tool_names
                if n in ("add_relationship", "remove_relationship")
            )
            total_tok = prompt_tokens + completion_tokens
            print(
                f"[HELPER:{helper_name}] call #{calls} "
                f"({total_tok} tok, ~${call_cost:.4f}, total ${cost:.4f})"
                f": +{n_writes} el, {n_rels} rel"
                if n_writes or n_rels
                else f"",
                flush=True,
            )

        # Post helper's messages to shared bus so main agents see results
        shared_messages.extend(local_messages)

        print(
            f"[HELPER:{helper_name}] Done — {calls} calls, "
            f"{writes} writes, ${cost:.4f}",
            flush=True,
        )

        logger.info(
            "helper_done",
            name=helper_name,
            calls=calls,
            writes=writes,
            cost=f"${cost:.4f}",
        )

        return {
            "helper": helper_name,
            "calls": calls,
            "writes": writes,
            "cost": cost,
        }

    # ═══════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _route_supervisor(
        state: TeamState,
    ) -> Literal["supervisor", "finalize"]:
        """Route: loop back to supervisor or go to finalize."""
        status = state.get("status", "running")
        if status in ("complete", "timeout", "stalled"):
            return "finalize"
        return "supervisor"

    def _build_context(self, metrics: dict, agent: str) -> str:
        """Build a contextual message for the agent before its turn."""
        total = metrics.get("total_elements", 0)
        rels = metrics.get("total_relationships", 0)
        ci = metrics.get("connectivity_index", 0)
        orphans = metrics.get("orphan_elements", 0)
        aspects = metrics.get("aspects", {})
        by_status = metrics.get("by_status", {})

        # Count elements by status for incremental mode guidance
        draft_count = by_status.get("draft", 0)
        reviewed_count = by_status.get("reviewed", 0)
        confirmed_count = by_status.get("confirmed", 0)

        status_line_parts = []
        if draft_count:
            status_line_parts.append(f"DRAFT={draft_count}")
        if reviewed_count:
            status_line_parts.append(f"reviewed={reviewed_count}")
        if confirmed_count:
            status_line_parts.append(f"confirmed={confirmed_count}")
        status_line = ", ".join(status_line_parts) if status_line_parts else ""

        missing = self._get_missing_aspects(aspects)

        # Determine mode: full run (many draft, few reviewed) or incremental
        is_incremental = reviewed_count > draft_count and reviewed_count > 5

        if agent == "agent_1":
            if missing:
                missing_str = ", ".join(f"{a} ({c})" for a, c in missing.items())
                return (
                    f"[Supervisor → Agent 1] YOUR TURN.\n"
                    f"State: {total} elements, {rels} relationships, "
                    f"CI={ci:.2f}, orphans={orphans}.\n"
                    f"Statuses: {status_line}.\n"
                    f"MISSING ASPECTS: {missing_str}. Create elements for these.\n"
                    f"After creating, call run_metrics to check progress.\n"
                    f"If orphans > 0 — stop creating, let Agent 2 link them."
                )
            elif is_incremental and draft_count > 0:
                return (
                    f"[Supervisor → Agent 1] YOUR TURN.\n"
                    f"State: {total} elements, {rels} relationships, "
                    f"CI={ci:.2f}, orphans={orphans}.\n"
                    f"Statuses: {status_line}.\n"
                    f"INCREMENTAL MODE: {reviewed_count} elements are reviewed. "
                    f"Focus on {draft_count} DRAFT elements — review their content, "
                    f"add missing relationships, and promote to reviewed.\n"
                    f"Do NOT modify reviewed elements unless adding relationships.\n"
                    f"When all drafts are resolved — call report_complete."
                )
            else:
                target_ci = self._user_ci_threshold if self._user_ci_threshold else 0.7
                return (
                    f"[Supervisor → Agent 1] YOUR TURN.\n"
                    f"State: {total} elements, {rels} relationships, "
                    f"CI={ci:.2f}, orphans={orphans}.\n"
                    f"Statuses: {status_line}.\n"
                    f"All aspects covered. If CI < {target_ci} — "
                    f"create more relationships.\n"
                    f"If orphans > 0 — let Agent 2 link them.\n"
                    f"If ready — call report_complete."
                )
        else:  # agent_2
            orphan_ids = self._get_orphan_ids()
            orphan_ids_str = (
                ", ".join(orphan_ids[:50])
                if orphan_ids
                else "(none — all elements linked)"
            )
            orphan_excess = ""
            if len(orphan_ids) > 50:
                orphan_excess = (
                    f" (+{len(orphan_ids) - 50} more — call run_metrics after linking)"
                )

            # Unparented elements: elements without parent in hierarchical aspects
            unparented = metrics.get("unparented_elements", 0)
            unparented_by_aspect = metrics.get("unparented_by_aspect", {})
            unparented_lines = ""
            if unparented > 0:
                # Get actual unparented IDs to give agent concrete targets
                unparented_ids = self._get_unparented_ids()
                sample = unparented_ids[:10]
                unparented_lines = (
                    f"\nUNPARENTED ELEMENTS: {unparented} total. "
                    f"First {len(sample)} IDs: {', '.join(sample)}"
                )
                if len(unparented_ids) > 10:
                    unparented_lines += f" (+{len(unparented_ids)-10} more)"
                unparented_lines += (
                    f"\nBy aspect: {', '.join(f'{a}={c}' for a,c in sorted(unparented_by_aspect.items()) if c>0)}."
                    f"\nHOW TO FIX: For each unparented ID, call "
                    f"write_element(id='XXX', parent='PARENT_ID'). "
                    f"Only id and parent are needed — other fields auto-inherit."
                    f"\nCRITICAL: Fix ALL unparented elements FIRST."
                )

            first_id = orphan_ids[0] if orphan_ids else "NONE"
            return (
                f"[Supervisor → Agent 2] YOUR TURN.\n"
                f"State: {total} elements, {rels} relationships, "
                f"CI={ci:.2f}, orphans={orphans}.{unparented_lines}\n"
                f"ORPHAN IDs (elements with NO relationships): "
                f"{orphan_ids_str}{orphan_excess}\n"
                f"Your PRIMARY task: fix unparented elements FIRST, "
                f"then link orphan IDs via add_relationship.\n"
                f'For each orphan: call read_element(id="{first_id}", '
                f"deep=true),\n"
                f"understand its content, then find matching elements "
                f"via read_element\n"
                f"and call add_relationship. Do NOT create new elements "
                f"unless essential.\n"
                f"Call run_metrics to track progress. "
                f"When orphans == 0 AND unparented == 0 — call report_complete."
            )

    def _get_missing_aspects(self, aspects: dict) -> dict:
        """Return aspects with 0 elements."""
        all_aspects = {
            "modules",
            "user_scenarios",
            "user_interface",
            "data_entities",
            "non_functional",
            "implementation",
            "metrics",
        }
        return {a: 0 for a in all_aspects if aspects.get(a, 0) == 0}

    def _get_orphan_ids(self) -> list[str]:
        """Return IDs of elements with no connections."""
        from src.storage.queries import get_orphan_ids

        return get_orphan_ids(self._storage)

    def _get_unparented_ids(self) -> list[str]:
        """Return IDs of elements without parent (excluding SRC and root types).

        Root types are derived from methodology: first element_type in each aspect.
        """
        from src.config.methodology import get_root_types, load_methodology

        _ROOT_TYPES: set[str] = set()
        try:
            method_path = getattr(self._storage, '_aspects_path', Path.cwd() / "aspects")
            method_path = Path(method_path).parent / "methodology.yaml"
            if method_path.exists():
                method = load_methodology(method_path)
                _ROOT_TYPES = get_root_types(method)
        except Exception:
            pass
        if not _ROOT_TYPES:
            _ROOT_TYPES = {"source"}

        ids = []
        for summary in self._storage.list_all():
            if summary.id.startswith("SRC-"):
                continue
            if summary.element_type in _ROOT_TYPES:
                continue
            if summary.parent:
                continue
            ids.append(summary.id)
        return ids

    @staticmethod
    def _has_report_complete(state: TeamState) -> bool:
        """Check if any agent called report_complete in recent tool calls."""
        # Scan all messages (not just last assistant) because agents
        # often follow report_complete with summary text (no tool_calls)
        for msg in reversed(state.get("messages", [])):
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    if tc.get("name") == "report_complete":
                        return True
        return False

    # ═══════════════════════════════════════════════════════════════
    # Finalize
    # ═══════════════════════════════════════════════════════════════

    async def _finalize_node(self, state: TeamState) -> dict:
        """Finalize: rebuild hierarchy, promote statuses, compute final metrics."""
        from src.storage.queries import promote_drafts_to_reviewed

        logger.info("team_finalize_start", status=state.get("status", "unknown"))

        # ── Step 1: Promote draft → reviewed ──
        finalized = promote_drafts_to_reviewed(self._storage)

        # ── Step 2: Final metrics ──
        try:
            metrics = compute_metrics(self._storage)
            state["last_metrics"] = metrics.model_dump()
        except Exception:
            pass

        m = state.get("last_metrics", {})
        cost = state.get("agent1_cost", 0) + state.get("agent2_cost", 0)
        a1_calls = state.get("agent1_calls", 0)
        a2_calls = state.get("agent2_calls", 0)
        round_num = state.get("round_num", 0)

        # Structured log for machine consumption
        logger.info(
            "run_summary",
            status=state.get("status", "unknown"),
            elements=m.get("total_elements", 0),
            relationships=m.get("total_relationships", 0),
            connectivity=round(m.get("connectivity_index", 0), 4),
            orphans=m.get("orphan_elements", 0),
            promoted_draft_to_reviewed=finalized,
            rounds=round_num,
            agent1_calls=a1_calls,
            agent2_calls=a2_calls,
            total_calls=a1_calls + a2_calls,
            cost_usd=round(cost, 4),
            aspects=m.get("aspects", {}),
            statuses=m.get("by_status", {}),
        )

        # Human-readable console output
        print(f"\n{'=' * 60}")
        print(f"Team finished: {state.get('status', 'unknown')}")
        print(f"  Elements: {m.get('total_elements', '?')}")
        print(f"  Relationships: {m.get('total_relationships', '?')}")
        print(f"  Connectivity: {m.get('connectivity_index', '?')}")
        print(f"  Orphans: {m.get('orphan_elements', '?')}")
        print(f"  Promoted draft→reviewed: {finalized}")
        print(f"  Rounds: {round_num}")
        print(f"  Total cost: ${cost:.4f}")
        print(f"  Agent 1 calls: {a1_calls}")
        print(f"  Agent 2 calls: {a2_calls}")

        # Per-aspect summary
        aspects = m.get("aspects", {})
        if aspects:
            print(f"\n  Aspects:")
            for aspect, count in sorted(aspects.items()):
                print(f"    {aspect}: {count} elements")

        # Status breakdown
        by_status = m.get("by_status", {})
        if by_status:
            status_line = ", ".join(f"{s}: {c}" for s, c in sorted(by_status.items()))
            print(f"\n  Statuses: {status_line}")

        print(f"{'=' * 60}\n", flush=True)

        return {
            "status": "complete",
            "last_metrics": state.get("last_metrics"),
        }

    # ═══════════════════════════════════════════════════════════════
    # Checkpoint persistence (crash recovery)
    # ═══════════════════════════════════════════════════════════════

    def _checkpoint_path(self) -> Path | None:
        """Path to the checkpoint file in the project directory."""
        if self._project_path:
            pp = Path(self._project_path) if not isinstance(self._project_path, Path) else self._project_path
            return pp / ".spec-editor-checkpoint.json"
        return None

    def _save_checkpoint(self, state: TeamState) -> None:
        """Save current state to checkpoint file for crash recovery.

        Only saves lightweight fields (no full message history to avoid
        large files). Messages are truncated to last 50 entries.
        """
        path = self._checkpoint_path()
        if not path:
            return

        try:
            # Truncate messages to avoid huge checkpoint files
            messages = list(state.get("messages", []))
            if len(messages) > 50:
                messages = messages[-50:]

            data = {
                "messages": messages,
                "total_calls": state.get("total_calls", 0),
                "total_writes": state.get("total_writes", 0),
                "total_cost": state.get("total_cost", 0.0),
                "agent1_calls": state.get("agent1_calls", 0),
                "agent2_calls": state.get("agent2_calls", 0),
                "agent1_cost": state.get("agent1_cost", 0.0),
                "agent2_cost": state.get("agent2_cost", 0.0),
                "last_metrics": state.get("last_metrics"),
                "round_num": state.get("round_num", 0),
                "status": state.get("status", "running"),
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, default=str)
        except Exception as exc:
            logger.warning("checkpoint_save_error", error=str(exc))

    def _load_checkpoint(self) -> dict | None:
        """Load state from checkpoint file. Returns None if no checkpoint."""
        path = self._checkpoint_path()
        if not path or not path.exists():
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.loads(f.read())
        except Exception as exc:
            logger.warning("checkpoint_load_error", error=str(exc))
            return None

    def _delete_checkpoint(self) -> None:
        """Delete checkpoint file after successful run."""
        path = self._checkpoint_path()
        if path and path.exists():
            try:
                path.unlink()
            except Exception:
                pass

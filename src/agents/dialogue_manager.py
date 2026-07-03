"""Dialogue manager with parallel agents and shared message bus."""

import asyncio
import json
import time
from pathlib import Path
from typing import Callable

from src.agents.dialogue.bus import MessageBus
from src.agents.dialogue.logger import DialogueLogger
from src.agents.dialogue.orchestrator import DialogueOrchestrator
from src.agents.dialogue.pool import AgentPool
from src.agents.dialogue.result import DialogueResult
from src.agents.orchestrator import OrchestratorAgent, OrchestratorDecision
from src.agents.provider import AgentProvider, AgentRunResult
from src.agents.spec_agent import SpecAgent
from src.config import get_logger
from src.config.settings import AgentsConfig
from src.mcp.metrics import compute_metrics
from src.providers.base import Message, MessageRole, ToolCall
from src.storage.adapter import StorageAdapter

logger = get_logger(__name__)


class DialogueManager:
    """Two agents work in parallel via a shared message bus."""

    def __init__(
        self,
        agent_1: AgentProvider,
        agent_2: AgentProvider,
        orchestrator: OrchestratorAgent,
        storage: StorageAdapter,
        config: AgentsConfig,
        log_dir: Path | None = None,
    ) -> None:
        self._agent_1 = agent_1
        self._agent_2 = agent_2
        self._orchestrator = orchestrator
        self._storage = storage
        self._config = config
        self._orch = DialogueOrchestrator(
            provider=orchestrator._provider
            if hasattr(orchestrator, "_provider")
            else None,
            storage=storage,
            methodology=None,
        )
        self._dialogue_logger: DialogueLogger | None = None
        self._tasks: list[asyncio.Task] = []
        self._helper_counter = 0
        # Parameters for creating helpers
        self._provider_ref = agent_1._provider
        self._methodology_ref = agent_1.get_methodology()
        self._source_dir_ref = agent_1.get_source_dir()
        if log_dir:
            self._dialogue_logger = DialogueLogger(log_dir / "dialogue.jsonl")
        # Async questions from agents
        self._project_path = log_dir  # project path for QuestionList
        self._delivered_questions: set[str] = set()

    async def run(
        self,
        initial_task: str,
        on_round: Callable | None = None,
        on_orchestrator: Callable | None = None,
    ) -> DialogueResult:
        bus = MessageBus()
        stop_event = asyncio.Event()
        self._bus = bus
        self._stop_event = stop_event

        # Create spawner and rebuild agents with it
        from src.agents.orchestrator import OrchestratorAgent as OA
        from src.agents.tools import build_all_handlers, get_tool_definitions

        async def _spawn(role: str, task: str) -> str:
            return await self.spawn_helper(role, task)

        # Recreate agents with spawner in tools (only for LoopAgent; LangGraphAgent is stateless)
        from src.agents.langgraph_agent import LangGraphAgent

        def _recreate(agent):
            """Recreate SpecAgent with spawner; pass-through for LangGraphAgent."""
            if isinstance(agent, LangGraphAgent):
                return agent
            return type(agent)(
                name=agent.name,
                provider=self._provider_ref,
                storage=self._storage,
                methodology=self._methodology_ref,
                source_dir=self._source_dir_ref,
                spawner=_spawn,
            )

        self._agent_1 = _recreate(self._agent_1)
        self._agent_2 = _recreate(self._agent_2)

        start_time = time.monotonic()
        max_time = self._config.max_time_minutes * 60

        # Launch both agents in parallel
        self._tasks = []
        agent1_task = asyncio.create_task(
            self._agent_loop(
                self._agent_1, "Agent 1", bus, stop_event, initial_message=initial_task
            )
        )
        # Agent 2 gets its own focused task for cross-aspect relationships
        cross_task = (
            "Your PRIMARY job: create cross-aspect RELATIONSHIPS via add_relationship. "
            "You CAN create elements if needed, but PREFER linking existing ones.\n\n"
            "ALGORITHM:\n"
            "1. Call run_metrics. Note orphan_elements count.\n"
            "2. If orphans > 0: list_all_elements, read orphans one by one, "
            "find matching elements in other aspects via read_element, "
            "link them via add_relationship. Repeat until orphans == 0.\n"
            "3. If connectivity < 0.7: find weakly-connected elements and link them.\n"
            "4. Call run_metrics to verify. If all good — report_complete.\n\n"
            "SCN -> UI via interacts_with. NFR -> MOD via applies_to. "
            "IMP -> MOD/SCN via implements. MET -> ENT/MOD via measures.\n\n"
            "CRITICAL: Read 2 elements, link immediately, then next 2. "
            "Do NOT read all first. Track progress via run_metrics."
        )
        agent2_task = asyncio.create_task(
            self._agent_loop(
                self._agent_2, "Agent 2", bus, stop_event, initial_message=cross_task
            )
        )
        self._tasks = [agent1_task, agent2_task]

        # Orchestrator checks periodically
        round_num = 0
        last_msg_count = 0
        stall_since = time.monotonic()
        try:
            while not stop_event.is_set():
                await asyncio.sleep(10)  # check every 10 seconds

                if time.monotonic() - start_time > max_time:
                    self._final_status = "timeout"
                    stop_event.set()
                    break

                history = await bus.get_history()

                # Inject answers to agent questions (questions.jsonl)
                await self._inject_answered_questions(bus)

                # Stall detector: no new messages > 60 seconds
                if len(history) == last_msg_count and len(history) >= 2:
                    if time.monotonic() - stall_since > 60:
                        # Check if agents are alive
                        alive = sum(1 for t in self._tasks if not t.done())
                        dead = len(self._tasks) - alive
                        # Look at metrics
                        try:
                            m = compute_metrics(self._storage)
                            metrics_str = (
                                f"Elements: {m.total_elements}, relationships: {m.total_relationships}, "
                                f"connectivity: {m.connectivity_index:.4f}, orphans: {m.orphan_elements}"
                            )
                        except Exception:
                            metrics_str = "metrics unavailable"

                        logger.warning(
                            "dialogue_stalled",
                            msgs=len(history),
                            alive=alive,
                            dead=dead,
                            metrics=metrics_str,
                        )

                        if alive == 0:
                            # All agents dead — finalize elements, then force-complete
                            await self._finalize_elements("stalled_all_dead")

                            from src.agents.tools import report_complete as rc_tool

                            rc_result = await rc_tool(storage=self._storage)
                            rc_content = json.dumps(rc_result)

                            # Post report_complete from orchestrator so _has_declared sees it
                            rc_msg = Message(
                                role=MessageRole.ASSISTANT,
                                content="All agents stopped. Orchestrator declaring completion.",
                                tool_calls=[
                                    ToolCall(
                                        id="orch-rc",
                                        name="report_complete",
                                        arguments={},
                                    )
                                ],
                                name="orchestrator",
                            )
                            async with bus._condition:
                                bus.post(rc_msg)
                            rc_result_msg = Message(
                                role=MessageRole.TOOL,
                                content=rc_content,
                                tool_call_id="orch-rc",
                            )
                            async with bus._condition:
                                bus.post(rc_result_msg)
                                bus._condition.notify_all()
                            logger.info(
                                "orchestrator_force_complete", result=rc_content[:200]
                            )

                            self._final_status = "complete"
                            stop_event.set()
                            break

                        # Wake up live agents
                        agent_status = (
                            f"Active agents: {alive}. "
                            if alive and not dead
                            else f": {alive}, dead: {dead}. "
                            if dead
                            else ""
                        )

                        # If orphans > 0, give explicit linking instructions
                        try:
                            m = compute_metrics(self._storage)
                            orphan_ids = self._get_orphan_ids()
                        except Exception:
                            orphan_ids = []

                        if orphan_ids:
                            sample_ids = ", ".join(orphan_ids[:20])
                            wake_text = (
                                f"[] Dialogue stalled. {agent_status}"
                                f"State: Elements: {m.total_elements}, relationships: {m.total_relationships}, "
                                f"connectivity: {m.connectivity_index:.4f}, orphans: {m.orphan_elements}.\n"
                                f"CRITICAL: {m.orphan_elements} orphan elements have NO connections!\n"
                                f"Orphan IDs (first 20): {sample_ids}\n"
                                f"YOUR TASK: Read each orphan via read_element, find what it relates to, "
                                f"and call add_relationship. Do NOT create new elements. "
                                f"Do NOT call read_source_document. Only read_element + add_relationship. "
                                f"Work through ALL orphans until run_metrics shows orphan_elements == 0."
                            )
                        else:
                            wake_text = (
                                f"[] Dialogue stalled. {agent_status}"
                                f"State: {metrics_str}. "
                                f"{self._src_coverage_str()}"
                                "Continue working. "
                                "If there are uncovered SRCs — create elements with derived_from. "
                                "If everything is ready — call report_complete."
                            )
                        wake_msg = Message(
                            role=MessageRole.USER,
                            content=wake_text,
                            name="orchestrator",
                        )
                        async with bus._condition:
                            bus.post(wake_msg)
                            bus._condition.notify_all()
                        # Write to log
                        if self._dialogue_logger:
                            self._dialogue_logger.log_orchestrator(
                                "health_check", wake_text, alive
                            )
                        stall_since = time.monotonic()
                else:
                    last_msg_count = len(history)
                    stall_since = time.monotonic()

                if len(history) < 2:
                    continue  # no dialogue yet

                # Count a new round when both have written
                names_in_last_two = {m.name for m in history[-2:] if m.name}
                if len(names_in_last_two) >= 2:
                    round_num += 1
                    # Show dialogue: last message from each main agent
                    if on_round:
                        msg_a1 = _last_msg_from(history, "Agent 1")
                        msg_a2 = _last_msg_from(history, "Agent 2")
                        on_round(round_num, msg_a1, msg_a2)

                decision, reason = self._orch.evaluate(
                    round_num, self._config.max_rounds, history
                )
                if on_orchestrator:
                    on_orchestrator(decision, reason)
                if self._dialogue_logger:
                    self._dialogue_logger.log_orchestrator(
                        decision.value, reason, len(self._tasks)
                    )

                if decision == OrchestratorDecision.COMPLETE:
                    self._final_status = "complete"
                    stop_event.set()
                elif decision == OrchestratorDecision.CONFLICT:
                    self._final_status = "conflict"
                    stop_event.set()

                logger.info(
                    "round_check",
                    round=round_num,
                    msgs=len(history),
                    decision=decision.value,
                )

        finally:
            stop_event.set()
            # Wait for both agents to finish
            await asyncio.gather(*self._tasks, return_exceptions=True)

        history = await bus.get_history()
        # Status — from what the orchestrator decided (don't recompute from history)
        status = getattr(self, "_final_status", "stopped")

        # Finalize element statuses and auto-link remaining orphans
        await self._finalize_elements(status)

        return self._make_result(status, round_num, history)

    async def spawn_helper(self, role: str, task: str) -> str:
        """Create and run a helper agent. Max 8 agents at once."""
        # Clean up completed tasks
        self._tasks = [t for t in self._tasks if not t.done()]
        if len(self._tasks) >= 8:  # SPEC_EDITOR__MAX_AGENTS
            raise RuntimeError(f"Agent limit reached (8). Wait for helpers to finish.")
        self._helper_counter += 1
        helper_name = f"Helper-{role}-{self._helper_counter}"
        helper = self._make_helper_agent(helper_name, role_name=role)
        helper_task = asyncio.create_task(
            self._agent_loop(
                helper,
                helper_name,
                self._bus,
                self._stop_event,
                is_helper=True,
                initial_message=f"Your role: {role}. Task: {task}. Read the dialogue history and join the work.",
            )
        )
        self._tasks.append(helper_task)
        logger.info("helper_spawned", name=helper_name, role=role)
        return helper_name

    def _make_helper_agent(self, name: str, role_name: str = "") -> SpecAgent:
        """Create a helper agent. If role_name matches a skill, use its prompt + tools."""
        from src.agents.spec_agent import SpecAgent as SA
        from src.config.skills import SkillsRegistry

        skill_role = None
        if role_name:
            skills_paths: list[Path] = []
            if self._project_path:
                skills_dir = self._project_path / "skills"
                file_path = self._project_path / "skills.yaml"
                if skills_dir.is_dir():
                    skills_paths.append(skills_dir)
                    if file_path.exists():
                        skills_paths.append(file_path)
                elif file_path.exists():
                    skills_paths.append(file_path)
            if skills_paths:
                from src.config.skills import SkillsRegistry

                registry = SkillsRegistry(skills_paths)
                skill = registry.get(role_name)
                if skill:
                    from src.agents.role import AgentRole

                    skill_role = AgentRole.from_skill(
                        skill, writable=True, default_prompt=""
                    )
                    logger.info("helper_skill_loaded", name=name, skill=role_name)

        return SA(
            name=name,
            provider=self._provider_ref,
            storage=self._storage,
            methodology=self._methodology_ref,
            source_dir=self._source_dir_ref,
            role=skill_role,
        )

    async def _agent_loop(
        self,
        agent: AgentProvider,
        name: str,
        bus: MessageBus,
        stop: asyncio.Event,
        initial_message: str | None = None,
        is_helper: bool = False,
    ) -> None:
        """Main agent loop: wait → think → respond.
        If is_helper=True — exits after completing its task."""
        last_seen = 0

        # Trace callback for detailed logging
        if self._dialogue_logger:
            _logger_ref = self._dialogue_logger
            _trace = lambda msg: _logger_ref.log_trace(msg)
        else:
            _trace = None

        # First message
        if initial_message:
            try:
                response = await agent.run(initial_message, trace_callback=_trace)
            except Exception as exc:
                logger.error("agent_error", agent=name, error=str(exc))
                return

            # Skip empty responses (reasoning models may return reasoning_content
            # with empty content and no tool_calls — DeepSeek API rejects these)
            if not response.content and not response.tool_calls:
                logger.info(
                    "agent_empty_response",
                    agent=name,
                    reason="no content and no tool_calls — skipping bus post",
                )
                # Helpers exit immediately, main agents wait for orchestrator wake-up
                if is_helper:
                    return
            else:
                msg = Message(
                    role=MessageRole.ASSISTANT,
                    content=response.content or "",
                    tool_calls=response.tool_calls,
                    name=name,
                )
                async with bus._condition:
                    bus.post(msg)
                    bus._condition.notify_all()

                # Post tool summary so colleague sees what was done
                summary = _format_tool_summary(response.tool_calls, [])
                if summary:
                    summary_msg = Message(
                        role=MessageRole.USER,
                        content=summary,
                        name=name,
                    )
                    async with bus._condition:
                        bus.post(summary_msg)
                        bus._condition.notify_all()

                if self._dialogue_logger:
                    self._dialogue_logger.log_message(
                        name,
                        msg.content or "",
                        [tc.model_dump() for tc in (msg.tool_calls or [])],
                        received_message=initial_message[:500] if initial_message else "",
                    )

            last_seen = len(bus._messages)

            # Force-stop on first message: agent killed by limits
            init_text = response.content or ""
            if any(
                s in init_text
                for s in ("IDLE_TIMEOUT", "LLM call limit reached", "STOP: cost")
            ):
                logger.info("agent_force_stopped", agent=name, reason=init_text[:100])
                return

            # Helpers exit after completing their task
            if is_helper:
                farewell = Message(
                    role=MessageRole.ASSISTANT,
                    content=f"[{name}] Task completed. Shutting down.",
                    name=name,
                )
                async with bus._condition:
                    bus.post(farewell)
                    bus._condition.notify_all()
                if self._dialogue_logger:
                    self._dialogue_logger.log_message(name, farewell.content, [])
                logger.info("helper_finished", name=name)
                return

        while not stop.is_set():
            try:
                # Wait for a new message from colleague
                last_seen, content = await bus.wait_for_others(name, last_seen)
            except Exception:
                break

            if stop.is_set():
                break

            try:
                # Build progress-aware prompt with metrics
                try:
                    m = compute_metrics(self._storage)
                    progress = (
                        f"YOUR PROGRESS: {m.total_elements} elements, "
                        f"{m.total_relationships} relationships, "
                        f"connectivity={m.connectivity_index:.2f}, "
                        f"orphans={m.orphan_elements}. "
                        f"Aspects: {m.aspects}. "
                        f"Statuses: {m.by_status}."
                    )
                except Exception:
                    progress = ""

                prompt = (
                    f"{progress}\n\n"
                    f"Your colleague responded:\n\n{content}\n\nAnalyse and respond."
                )
                response = await agent.run(
                    prompt, await bus.get_history(), trace_callback=_trace
                )
            except Exception as exc:
                logger.error("agent_error", agent=name, error=str(exc))
                break

            # Skip empty responses (reasoning models: reasoning_content without content)
            if not response.content and not response.tool_calls:
                logger.info(
                    "agent_empty_response",
                    agent=name,
                    reason="no content and no tool_calls — skipping bus post",
                )
                last_seen = len(bus._messages)
                continue

            msg = Message(
                role=MessageRole.ASSISTANT,
                content=response.content or "",
                tool_calls=response.tool_calls,
                name=name,
            )
            async with bus._condition:
                bus.post(msg)
                bus._condition.notify_all()

            # Post a summary of tool actions so colleague sees what changed
            summary = _format_tool_summary(response.tool_calls, await bus.get_history())
            if summary:
                summary_msg = Message(
                    role=MessageRole.USER,
                    content=summary,
                    name=name,
                )
                async with bus._condition:
                    bus.post(summary_msg)
                    bus._condition.notify_all()

            if self._dialogue_logger:
                self._dialogue_logger.log_message(
                    name,
                    msg.content or "",
                    [tc.model_dump() for tc in (msg.tool_calls or [])],
                    received_message=content[:500] if content else "",
                )

            # Force-stop signals: agent was killed by limits, exit the loop
            text = response.content or ""
            if any(
                s in text
                for s in ("IDLE_TIMEOUT", "LLM call limit reached", "STOP: cost")
            ):
                logger.info("agent_force_stopped", agent=name, reason=text[:100])
                break

            if self._has_report_complete(response):
                # Notify colleague that we're done, but don't stop —
                # let the orchestrator decide
                pass

    @staticmethod
    def _has_report_complete(response) -> bool:
        if response.tool_calls:
            for tc in response.tool_calls:
                if tc.name == "report_complete":
                    return True
        # Also treat completion phrases in text
        text = (response.content or "").lower()
        complete_phrases = [
            "propose completion",
            "complete work",
            "ready to complete",
        ]
        return any(phrase in text for phrase in complete_phrases)

    async def _inject_answered_questions(self, bus) -> None:
        """Check questions.jsonl and deliver answers to agents via the bus."""
        from src.agents.questions import QuestionList

        if not self._project_path:
            return

        ql = QuestionList(self._project_path)
        all_questions = ql._all()

        for q in all_questions:
            if q.status == "answered" and q.id not in self._delivered_questions:
                self._delivered_questions.add(q.id)
                answer_text = f"[]   {q.id} «{q.question}»  : {q.answer}"
                answer_msg = Message(
                    role=MessageRole.USER,
                    content=answer_text,
                    name="orchestrator",
                )
                async with bus._condition:
                    bus.post(answer_msg)
                    bus._condition.notify_all()
                if self._dialogue_logger:
                    self._dialogue_logger.log_orchestrator(
                        "question_answered", answer_text, 0
                    )

    async def _finalize_elements(self, status: str) -> None:
        """Finalize element statuses and auto-link remaining orphans.

        Called when dialogue ends (complete/timeout/stalled).
        - Promotes draft elements to reviewed
        - Auto-links remaining orphan elements via add_relationship
        """
        from src.storage.queries import promote_drafts_to_reviewed

        logger.info("finalize_start", status=status)

        finalized_count = promote_drafts_to_reviewed(self._storage)

        logger.info(
            "finalize_done",
            status=status,
            promoted=finalized_count,
        )
        if self._dialogue_logger:
            self._dialogue_logger.log_orchestrator(
                "finalize",
                f"Promoted {finalized_count} draft->reviewed",
                0,
            )

    def _get_orphan_ids(self) -> list[str]:
        """Return IDs of elements with no connections."""
        from src.storage.queries import get_orphan_ids

        return get_orphan_ids(self._storage)

    def _src_coverage_str(self) -> str:
        """Return a string describing SRC element coverage."""
        all_elements = self._storage.list_all()
        src_elements = [e for e in all_elements if e.id.startswith("SRC-")]
        spec_elements = [e for e in all_elements if not e.id.startswith("SRC-")]
        src_ids = {e.id for e in src_elements}
        covered = 0
        for se in spec_elements:
            try:
                full = self._storage.read_element(se.id)
                if full.derived_from:
                    covered += 1
                    continue
                for entries in (full.relationships or {}).values():
                    if any(e.target in src_ids for e in entries):
                        covered += 1
                        break
            except Exception:
                pass
        total_src = len(src_elements)
        total_spec = len(spec_elements)
        if total_src == 0:
            return "No SRC elements."
        return f"SRC coverage: {covered}/{total_spec} spec elements traced to {total_src} sources."

    def _make_result(
        self, status: str, rounds: int, history: list[Message]
    ) -> DialogueResult:
        if self._dialogue_logger:
            m = compute_metrics(self._storage)
            # Count by aspect
            aspects = {}
            for s in self._storage.list_all():
                aspects[s.aspect] = aspects.get(s.aspect, 0) + 1
            aspects_str = ", ".join(f"{k}: {v}" for k, v in sorted(aspects.items()))
            summary = (
                f"Dialogue ended: {status} ( {rounds})\n"
                f"Elements: {m.total_elements}, : {m.total_relationships}\n"
                f"By aspect: {aspects_str}"
            )
            self._dialogue_logger.log_orchestrator(
                "TERMINATED", summary, len(self._tasks)
            )
        return DialogueResult(
            status=status,
            rounds_completed=rounds,
            final_metrics=compute_metrics(self._storage),
            dialogue_history=history,
        )


def _last_msg_from(history: list, name: str):
    """Find the last message from the specified agent."""
    for msg in reversed(history):
        if msg.name == name and msg.content:
            return msg
    return None


def _format_tool_summary(tool_calls: list, history: list[Message]) -> str:
    """Build a human-readable summary of tool actions for the colleague.

    Looks at recent TOOL messages in history that correspond to these tool_calls.
    Returns empty string if nothing notable was done.
    """
    if not tool_calls:
        return ""

    import json

    parts = []
    for tc in tool_calls:
        # Find the matching TOOL result in recent history
        result = None
        for i in range(len(history) - 1, -1, -1):
            if history[i].role == MessageRole.TOOL and history[i].tool_call_id == tc.id:
                try:
                    result = json.loads(history[i].content or "{}")
                except Exception:
                    pass
                break

        if tc.name == "write_element":
            eid = result.get("element_id", "?") if result else "?"
            aspect = tc.arguments.get("aspect", "?")
            title = tc.arguments.get("title", "?")[:60]
            status = tc.arguments.get("status", "draft")
            parts.append(f'  + {eid} [{aspect}] "{title}" ({status})')

        elif tc.name == "add_relationship":
            src = tc.arguments.get("source_id", "?")
            rel = tc.arguments.get("rel_type", "?")
            tgt = tc.arguments.get("target_id", "?")
            parts.append(f"  ↪ {src} --{rel}--> {tgt}")

        elif tc.name == "remove_relationship":
            src = tc.arguments.get("source_id", "?")
            rel = tc.arguments.get("rel_type", "?")
            tgt = tc.arguments.get("target_id", "?")
            parts.append(f"  ✕ removed: {src} --{rel}--> {tgt}")

        elif tc.name == "delete_element":
            eid = tc.arguments.get("element_id", "?")
            parts.append(f"  ✕ deleted: {eid}")

        elif tc.name == "report_complete":
            decl = result.get("declaration", "?") if result else "?"
            parts.append(f"  ✓ report_complete: {decl}")

    if not parts:
        return ""

    return "[Tool results]\n" + "\n".join(parts)

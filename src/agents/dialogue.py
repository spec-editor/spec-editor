"""Dialogue manager with parallel agents and shared message bus."""

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from src.agents.orchestrator import OrchestratorAgent, OrchestratorDecision
from src.agents.spec_agent import SpecAgent
from src.config import get_logger
from src.config.settings import AgentsConfig
from src.mcp.metrics import MetricsReport, compute_metrics
from src.providers.base import Message, MessageRole
from src.storage.adapter import StorageAdapter
from src.tracing import implements

logger = get_logger(__name__)


class DialogueResult(BaseModel):
    status: str = Field(default="unknown")
    rounds_completed: int = 0
    final_metrics: MetricsReport | None = None
    dialogue_history: list[Message] = Field(default_factory=list)


class MessageBus:
    """Shared message bus between agents.

    Agents write messages, read new messages from their colleague.
    """

    def __init__(self) -> None:
        self._messages: list[Message] = []
        self._condition = asyncio.Condition()
        self._msg_count = 0  # how many messages the agent has already read

    def post(self, message: Message) -> None:
        self._messages.append(message)

    async def get_history(self) -> list[Message]:
        async with self._condition:
            return list(self._messages)

    async def wait_for_others(self, my_name: str, last_seen: int) -> tuple[int, str]:
        """Wait for a new message from another agent.

        Returns: (new index, message text)
        """
        async with self._condition:
            while True:
                # Look for new messages NOT from me
                for i in range(last_seen, len(self._messages)):
                    msg = self._messages[i]
                    if msg.name != my_name and (msg.content or msg.tool_calls):
                        return (i + 1, msg.content or "(executed tools)")
                # No new ones — wait
                await self._condition.wait()

    def notify_all(self) -> None:
        """Notify all waiting agents (call within lock)."""
        # condition.notify_all must be called when lock is held
        # this method is for external calls from post() followed by notify


class DialogueManager:
    """Two agents work in parallel via a shared message bus."""

    def __init__(
        self,
        agent_1: SpecAgent,
        agent_2: SpecAgent,
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
        self._methodology_ref = agent_1._get_methodology()
        self._source_dir_ref = agent_1._get_source_dir()
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

        # Recreate agents with spawner in tools
        self._agent_1 = type(self._agent_1)(
            name=self._agent_1.name,
            provider=self._provider_ref,
            storage=self._storage,
            methodology=self._methodology_ref,
            source_dir=self._source_dir_ref,
            spawner=_spawn,
        )
        self._agent_2 = type(self._agent_2)(
            name=self._agent_2.name,
            provider=self._provider_ref,
            storage=self._storage,
            methodology=self._methodology_ref,
            source_dir=self._source_dir_ref,
            spawner=_spawn,
        )

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
            "Read SCN-001 and SCR-001. If related, create: "
            "add_relationship(source_id='SCN-001', rel_type='interacts_with', target_id='SCR-001'). "
            "Repeat for all SCN with all SCR/SEC. Then NFR with MOD via applies_to. "
            "Then IMP with MOD via implements. Read 2 elements, link, next pair. "
            "Do NOT read all elements first — link as you go."
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
                            # All agents dead — finish
                            self._final_status = "stalled_all_dead"
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
                        wake_text = (
                            f"[] Dialogue stalled. {agent_status}"
                            f"State: {metrics_str}. "
                            f"{self._src_coverage_str()}"
                            "Continue working. "
                            "If there are uncovered SRCs — create elements with derived_from. "
                            "If there are orphans — link them. "
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
            skills_path = self._project_path / "skills.yaml" if self._project_path else None
            if skills_path and skills_path.exists():
                registry = SkillsRegistry(skills_path)
                skill = registry.get(role_name)
                if skill:
                    from src.agents.role import AgentRole
                    skill_role = AgentRole.from_skill(skill, writable=True, default_prompt="")
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
        agent: SpecAgent,
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
            msg = Message(
                role=MessageRole.ASSISTANT,
                content=response.content or "",
                tool_calls=response.tool_calls,
                name=name,
            )
            async with bus._condition:
                bus.post(msg)
                bus._condition.notify_all()
            if self._dialogue_logger:
                self._dialogue_logger.log_message(
                    name,
                    msg.content or "",
                    [tc.model_dump() for tc in (msg.tool_calls or [])],
                    received_message=initial_message[:500] if initial_message else "",
                )
            last_seen = len(bus._messages)

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
                prompt = (
                    f"Your colleague responded:\n\n{content}\n\nAnalyse and respond."
                )
                response = await agent.run(
                    prompt, await bus.get_history(), trace_callback=_trace
                )
            except Exception as exc:
                logger.error("agent_error", agent=name, error=str(exc))
                break

            msg = Message(
                role=MessageRole.ASSISTANT,
                content=response.content or "",
                tool_calls=response.tool_calls,
                name=name,
            )
            async with bus._condition:
                bus.post(msg)
                bus._condition.notify_all()
            if self._dialogue_logger:
                self._dialogue_logger.log_message(
                    name,
                    msg.content or "",
                    [tc.model_dump() for tc in (msg.tool_calls or [])],
                    received_message=content[:500] if content else "",
                )

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


def _compact_args(tool_name: str, args: dict) -> dict:
    """Keep only meaningful arguments of a tool call for the log."""
    # For write operations show ID and title
    if tool_name in ("write_element",):
        return {
            k: args.get(k, "")
            for k in ("id", "title", "aspect", "element_type")
            if k in args
        }
    # For relationships — source and target
    if tool_name in ("add_relationship", "remove_relationship"):
        return {
            k: args.get(k, "")
            for k in ("source_id", "target_id", "rel_type")
            if k in args
        }
    # For search — query
    if tool_name == "search_elements":
        return {"query": args.get("query", "")}
    # For metrics — don't show arguments
    if tool_name in ("run_metrics", "run_validate", "report_complete"):
        return {}
    # Others — show first 2 keys
    keys = list(args.keys())[:2]
    return {k: str(args[k])[:60] for k in keys}


def _last_msg_from(history: list, name: str):
    """Find the last message from the specified agent."""
    for msg in reversed(history):
        if msg.name == name and msg.content:
            return msg
    return None


# ======================================================================
# Dialogue logger
# ======================================================================


class DialogueLogger:
    def __init__(self, log_path: Path) -> None:
        self._path = log_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(log_path, "a", encoding="utf-8")

    def log_message(
        self,
        agent_name: str,
        content: str,
        tool_calls: list | None = None,
        received_message: str = "",
    ) -> None:
        # Shorten tool_calls arguments for readability
        compact_tools = []
        for tc in tool_calls or []:
            if isinstance(tc, dict):
                name = tc.get("name", "?")
                args = tc.get("arguments", {})
            else:
                name = getattr(tc, "name", "?")
                args = getattr(tc, "arguments", {})
            # Show only meaningful arguments
            short_args = _compact_args(name, args)
            compact_tools.append({"name": name, "args": short_args})

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": agent_name,
            "content": content[:3000] if content else "",
            "tool_calls": compact_tools,
            "received": received_message[:500] if received_message else "",
        }
        self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._file.flush()

    def log_orchestrator(
        self, decision: str, reason: str, agent_count: int = 0
    ) -> None:
        self._file.write(
            json.dumps(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "agent": f"Orchestrator ({agent_count} agents)"
                    if agent_count
                    else "Orchestrator",
                    "decision": decision,
                    "reason": reason,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        self._file.flush()

    def log_trace(self, message: str) -> None:
        """Log a detailed trace line from agent execution (tool calls, results)."""
        self._file.write(
            json.dumps(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "trace": message,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        self._file.flush()

    def close(self) -> None:
        self._file.close()


class AgentPool:
    """Parallel agent pool — spawn, limit, lifecycle."""

    def __init__(
        self, main_agents: list, bus, max_agents: int = 8, make_loop=None
    ) -> None:
        self._bus = bus
        self._max_agents = max_agents
        self._tasks: list = []
        self._counter = 0
        self._make_loop = make_loop

        stop_ev = asyncio.Event()
        for agent in main_agents:
            if make_loop:
                task = make_loop(
                    agent,
                    agent.name,
                    bus,
                    stop_ev,
                    initial_message=None,
                    is_helper=False,
                )
                if task:
                    self._tasks.append(task)

    async def spawn(self, role: str, task_desc: str) -> str:
        """Spawn a helper. Returns name."""
        self._cleanup()
        if len(self._tasks) >= self._max_agents:
            raise RuntimeError(f"agent limit reached ({self._max_agents})")
        self._counter += 1
        name = f"Helper-{role}-{self._counter}"
        if self._make_loop:
            # Create a fake agent for the helper
            task = self._make_loop(
                None,
                name,
                self._bus,
                asyncio.current_task() is None and asyncio.Event() or None,
                initial_message=f": {role}. : {task_desc}",
                is_helper=True,
            )
            if task:
                self._tasks.append(task)
        return name

    @property
    def active_count(self) -> int:
        self._cleanup()
        return len(self._tasks)

    def _cleanup(self) -> None:
        self._tasks = [t for t in self._tasks if not t.done()]


@implements("MOD-001-C3")
class DialogueOrchestrator:
    """Round evaluation, completion detection, declared check."""

    def __init__(self, provider, storage, methodology) -> None:
        self._provider = provider
        self._storage = storage
        self._methodology = methodology

    def evaluate(
        self, round_num: int, max_rounds: int, history: list[Message]
    ) -> tuple[OrchestratorDecision, str]:
        """Evaluate a round."""
        if round_num >= max_rounds:
            return OrchestratorDecision.COMPLETE, (
                f"Round limit reached ({max_rounds})"
            )

        a1_declared = self._has_declared(history, "Agent 1")
        a2_declared = self._has_declared(history, "Agent 2")

        if a1_declared and a2_declared:
            return OrchestratorDecision.COMPLETE, "Both agents confirmed completion"

        # LLM evaluation (in tests — mock)
        # In reality called via OrchestratorAgent.run()
        return OrchestratorDecision.CONTINUE, "continue"

    @staticmethod
    def _has_declared(history: list[Message], name: str) -> bool:
        """report_complete in tool_calls OR key phrases in text."""
        import json

        for i in range(len(history) - 1, -1, -1):
            msg = history[i]
            if msg.name != name:
                continue

            # Check tool_calls
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    if tc.name == "report_complete":
                        if (
                            i + 1 < len(history)
                            and history[i + 1].role == MessageRole.TOOL
                        ):
                            try:
                                result = json.loads(history[i + 1].content or "{}")
                                if result.get("declaration") == "rejected":
                                    return False
                            except Exception:
                                pass
                        return True
                return False

            # Check text for completion phrases
            text = (msg.content or "").lower()
            if any(
                p in text
                for p in (
                    "propose completion",
                    "complete work",
                    "ready to complete",
                )
            ):
                return True

        return False

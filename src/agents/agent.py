"""Unified Agent — combines task-queue worker + LLM dialogue capabilities.

Every agent (coding, tester, PM, devops, spec_update) inherits from this class.
Supports:
  - Redis task queue (subscribe + process)
  - LLM dialogue (system prompt + tools + context compaction)
  - Skill loading from skills/*.yaml via agents.yaml
  - Proactive health scanning (PM role)
  - Cost tracking and usage limits
  - Cross-agent communication via Redis event bus
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from src.agents.task_queue import (
    AbstractTaskQueue,
    Task,
    TaskResult,
    get_queue_url,
)
from src.tracing import StructuredLogEmitter


class Agent:
    """Unified agent base class.

    Args:
        name: Human-readable name (e.g. "Coding Agent").
        role: Queue role — ``"coding"``, ``"tester"``, ``"project-manager"``, ``"devops"``.
        project_path: Path to the spec-editor project.
        queue_url: Connection URL. Auto-detected from local.yaml if empty.
        system_prompt: LLM system prompt (for dialogue mode).
        tools: Tool definitions for LLM (dialogue mode).
        tool_handlers: Tool name → callable mapping.
        skills: List of skill names to load from skills/*.yaml.
        provider: Optional LLM provider (for dialogue mode).
        max_llm_calls: Hard limit on LLM calls per dialogue run.
        token_budget: Token budget for context compaction.
    """

    def __init__(
        self,
        name: str,
        role: str,
        project_path: str | Path,
        queue_url: str = "",
        *,
        system_prompt: str = "",
        tools: list[Any] | None = None,
        tool_handlers: dict[str, Callable] | None = None,
        skills: list[str] | None = None,
        provider: Any = None,
        max_llm_calls: int = 30,
        token_budget: int = 50000,
    ) -> None:
        self.name = name
        self.role = role
        self._project_path = Path(project_path)
        self._queue_url = queue_url or get_queue_url(project_path)

        # ── Ensure plugins are on sys.path (needed for spec_editor_cycle imports) ──
        try:
            from src.hooks import get_plugins

            get_plugins()  # triggers plugin discovery and sys.path setup
        except Exception:
            pass

        # ── Logging ──
        self._log = StructuredLogEmitter(
            module_id=f"MOD-{role}-agent",
            scenario_id=f"SCN-{role}-worker",
            log_dir=str(self._project_path / "logs"),
            auto_element=False,
        )

        # ── Skills ──
        self._skills: list[str] = skills or []
        self._skill_prompts: dict[str, str] = {}
        self._load_skills()

        # ── LLM (optional, for dialogue mode) ──
        self._provider = provider
        self._system_prompt = system_prompt
        self._tools = tools or []
        self._tool_handlers = tool_handlers or {}
        self._max_llm_calls = max_llm_calls
        self._token_budget = token_budget

        # ── Usage tracking ──
        self._usage_file = self._project_path / "tasks" / role / "usage.json"
        self._started_at = time.time()
        self._usage: dict[str, Any] = {}
        self._load_usage()

    # ═══════════════════════════════════════════════════════════════════════
    # Skills
    # ═══════════════════════════════════════════════════════════════════════

    def _load_skills(self) -> None:
        """Load skill prompts from skills/*.yaml based on configured skill names."""
        skills_dir = self._project_path / "skills"
        if not skills_dir.is_dir():
            return

        import yaml

        for skill_file in sorted(skills_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(skill_file.read_text()) or {}
                for skill_data in data.get("skills", []):
                    skill_name = skill_data.get("name", "")
                    if not self._skills or skill_name in self._skills:
                        prompt = skill_data.get("prompt", "")
                        if prompt:
                            self._skill_prompts[skill_name] = prompt
                            self._log.info(
                                "skill_loaded",
                                skill=skill_name,
                                file=str(skill_file.name),
                            )
            except Exception as exc:
                self._log.error(
                    "skill_load_error", file=str(skill_file.name), error=str(exc)
                )

    def get_skill_prompt(self, skill_name: str) -> str:
        """Get the prompt for a loaded skill."""
        return self._skill_prompts.get(skill_name, "")

    @property
    def loaded_skills(self) -> list[str]:
        return list(self._skill_prompts.keys())

    # ═══════════════════════════════════════════════════════════════════════
    # Queue mode — listen and process tasks
    # ═══════════════════════════════════════════════════════════════════════

    async def run_queue(self) -> None:
        """Connect to queue and process tasks forever (worker mode)."""
        queue: AbstractTaskQueue
        try:
            queue = AbstractTaskQueue.connect(self._queue_url)
        except ValueError as exc:
            self._log.error("queue_connect_failed", error=str(exc))
            return

        await queue.connect()
        self._log.info("worker_started", role=self.role, queue=self._queue_url)

        # PM and analyst-manager: start proactive scan
        scan_task: asyncio.Task | None = None
        if self.role in ("project-manager", "analyst-manager"):
            scan_task = asyncio.create_task(self._proactive_scan_loop())

        try:
            async for task in queue.subscribe(self.role):
                self._log.info(
                    "task_received",
                    task_id=task.task_id,
                    payload_keys=list(task.payload.keys()),
                )
                result = await self.handle_task(task)
                try:
                    await queue.ack(task, result)
                except Exception as ack_exc:
                    self._log.warning(
                        "ack_failed",
                        task_id=task.task_id,
                        error=str(ack_exc)[:120],
                    )
                self._log.info("task_done", task_id=task.task_id, status=result.status)
                self._update_usage(result)
        except asyncio.CancelledError:
            self._log.info("worker_cancelled", role=self.role)
        except Exception as exc:
            # Redis timeout/connection errors are recoverable — log and exit gracefully
            err_msg = str(exc).lower()
            if any(kw in err_msg for kw in ("timeout", "connection", "eof", "reset")):
                self._log.warning(
                    "worker_reconnect", role=self.role, error=str(exc)[:120]
                )
            else:
                self._log.error("worker_fatal", role=self.role, error=str(exc))
        finally:
            if scan_task:
                scan_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await scan_task
            await queue.close()

    async def handle_task(self, task: Task) -> TaskResult:
        """Handle a task. Override in subclasses for role-specific logic.

        Default: returns failed if no handler is registered.
        """
        # Try to handle via LLM if provider is configured
        if self._provider and self._tool_handlers:
            return await self._handle_via_llm(task)

        return TaskResult(
            task_id=task.task_id,
            role=self.role,
            status="failed",
            payload={"error": f"No handler for role: {self.role}"},
        )

    async def _handle_via_llm(self, task: Task) -> TaskResult:
        """Use LLM to process a task (dialogue mode)."""
        task_text = task.payload.get("task", "")
        try:
            response = await self.ask(task_text)
            return TaskResult(
                task_id=task.task_id,
                role=self.role,
                status="ok",
                payload={"content": response[:1000]},
            )
        except Exception as exc:
            return TaskResult(
                task_id=task.task_id,
                role=self.role,
                status="failed",
                payload={"error": str(exc)},
            )

    # ═══════════════════════════════════════════════════════════════════════
    # Dialogue mode — LLM conversation
    # ═══════════════════════════════════════════════════════════════════════

    async def ask(
        self,
        user_message: str,
        conversation_history: list[Any] | None = None,
    ) -> str:
        """Send a message to the LLM and get a text response.

        Requires ``provider`` to be set.
        """
        if not self._provider:
            raise RuntimeError(f"Agent '{self.name}' has no LLM provider")

        from src.agents.compaction import ContextCompactor
        from src.providers.base import Message, MessageRole

        compactor = ContextCompactor(
            max_llm_calls=self._max_llm_calls, token_budget=self._token_budget
        )

        messages: list[Message] = []

        # Build system prompt from skills + configured prompt
        system_text = self._build_system_prompt()
        messages.append(Message(role=MessageRole.SYSTEM, content=system_text))

        if conversation_history:
            for msg in conversation_history:
                if msg.role == MessageRole.TOOL:
                    continue
                messages.append(msg)

        messages.append(
            Message(role=MessageRole.USER, content=user_message, name="user")
        )

        tools = self._tools if self._provider.supports_tools() else None
        total_calls = 0
        hard_limit = self._max_llm_calls * 3

        while total_calls < hard_limit:
            response = await self._provider.complete(messages=messages, tools=tools)
            total_calls += 1
            compactor.record_llm_call(
                response.usage.prompt_tokens, response.usage.completion_tokens
            )

            if not response.tool_calls:
                return response.content or ""

            # Append assistant message with tool_calls (required by DeepSeek)
            messages.append(
                Message(
                    role=MessageRole.ASSISTANT,
                    content=response.content or "",
                    tool_calls=response.tool_calls,
                )
            )

            # Execute tools and append results
            for tc in response.tool_calls:
                handler = self._tool_handlers.get(tc.name)
                if handler is None:
                    result = {"error": f"Unknown tool: {tc.name}"}
                else:
                    try:
                        result = await _call_handler(handler, tc.arguments)
                    except Exception as exc:
                        result = {"error": str(exc)}
                        self._log.error(
                            "tool_error",
                            agent=self.name,
                            tool=tc.name,
                            error=str(exc),
                        )

                messages.append(
                    Message(
                        role=MessageRole.TOOL,
                        content=json.dumps(result, default=str),
                        name=tc.name,
                        tool_call_id=tc.id,
                    )
                )

        return "(LLM call limit reached)"

    def _build_system_prompt(self) -> str:
        """Build system prompt from configured prompt + loaded skills."""
        parts = [self._system_prompt] if self._system_prompt else []

        for skill_name, prompt in self._skill_prompts.items():
            parts.append(f"\n## Skill: {skill_name}\n\n{prompt}")

        return "\n\n".join(parts) if parts else "You are an AI agent."

    # ═══════════════════════════════════════════════════════════════════════
    # Usage tracking
    # ═══════════════════════════════════════════════════════════════════════

    def _load_usage(self) -> None:
        self._usage = {
            "tokens_in": 0,
            "tokens_out": 0,
            "tasks_done": 0,
            "started_at": self._started_at,
        }
        if self._usage_file.exists():
            try:
                saved = json.loads(self._usage_file.read_text())
                self._usage.update(saved)
                self._started_at = saved.get("started_at", self._started_at)
            except Exception:
                pass

    def _update_usage(self, result: TaskResult) -> None:
        tokens = result.payload.get("tokens", {})
        self._usage["tokens_in"] += tokens.get("input", 0) or tokens.get("in", 0)
        self._usage["tokens_out"] += tokens.get("output", 0) or tokens.get("out", 0)
        self._usage["tasks_done"] += 1
        self._usage["updated_at"] = time.time()
        self._usage_file.parent.mkdir(parents=True, exist_ok=True)
        self._usage_file.write_text(json.dumps(self._usage, indent=2))

    # ═══════════════════════════════════════════════════════════════════════
    # Proactive scan (PM role)
    # ═══════════════════════════════════════════════════════════════════════

    async def _proactive_scan_loop(self) -> None:
        """Background loop: run health checks every 60 seconds.

        PM: runs pm_checks (blocked bugs, build errors, etc.).
        Analyst-manager: scans for draft elements, moves to reviewed.
        """
        await asyncio.sleep(10)
        while True:
            try:
                if self.role == "project-manager":
                    from src.agents.pm_checks import run_proactive_scan
                    from src.storage.filesystem import FilesystemStorage

                    storage = FilesystemStorage(self._project_path)
                    # ── Health scan ──
                    counts = await run_proactive_scan(
                        storage, self._project_path, self._log
                    )
                    total = sum(counts.values())
                    if total > 0:
                        self._log.info(
                            "pm_scan_cycle",
                            total=total,
                            errors=counts.get("error", 0),
                            warnings=counts.get("warning", 0),
                        )

                    # ── Dispatch reviewed elements to coding queue ──
                    from src.agents.task_queue import Task
                    task = Task(
                        task_id=f"pm-dispatch-{id(asyncio.get_event_loop())}",
                        role="project-manager",
                        payload={"action": "scan_and_dispatch"},
                    )
                    result = await self.handle_task(task)
                    dispatched = result.payload.get("dispatched", 0)
                    if dispatched > 0:
                        self._log.info(
                            "pm_dispatch_cycle",
                            dispatched=dispatched,
                        )
                elif self.role == "analyst-manager":
                    from src.agents.task_queue import Task

                    task = Task(
                        task_id=f"am-scan-{id(asyncio.get_event_loop())}",
                        role="analyst-manager",
                        payload={"action": "scan_drafts"},
                    )
                    result = await self.handle_task(task)
                    if result.payload.get("draft_elements_refined", 0) > 0:
                        self._log.info(
                            "am_scan_cycle",
                            refined=result.payload["draft_elements_refined"],
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log.error("scan_error", error=str(exc), role=self.role)
            await asyncio.sleep(60)


async def _call_handler(handler: Callable, arguments: dict) -> Any:
    """Call a tool handler, supporting both sync and async."""
    result = handler(**arguments)
    if asyncio.iscoroutine(result):
        result = await result
    return result

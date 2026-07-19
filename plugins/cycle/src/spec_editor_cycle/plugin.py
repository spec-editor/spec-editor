"""Cycle plugin for spec-editor-core.

Adds the ingest cycle (log analysis → bug detection → PM Agent handoff),
persistent agents, and CLI commands (cycle, agent).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from src.hooks import SpecEditorPlugin


class CyclePlugin(SpecEditorPlugin):
    """Ingest cycle: reads logs, detects bugs, hands off to PM Agent."""

    # ── MCP tools ──────────────────────────────────────────────────

    def register_mcp_tools(self, storage, project_path: str) -> dict[str, Any]:
        """Register cycle MCP tools (run_cycle, run_log_analysis,
        ingest_bugs, update_spec_from_bugs).
        """
        try:
            from spec_editor_cycle.tools import build_cycle_handlers
            return build_cycle_handlers(storage, project_path)
        except ImportError:
            return {}

    # ── CLI commands ───────────────────────────────────────────────

    def register_cli_commands(self, cli_group) -> None:
        """Register ``spec-editor cycle`` and ``spec-editor agent`` commands."""
        from spec_editor_cycle.cli import register_commands
        register_commands(cli_group)

    # ── Run modes ──────────────────────────────────────────────────

    def on_run(
        self, mode, project_path, storage, method, agents_config, settings, initial_task,
    ) -> Any:
        """Start coding team as background task if Pro license is present.

        Analytics team always runs — this plugin never blocks it.
        Coding team runs in parallel via asyncio.Task.

        Returns:
            asyncio.Task if coding team started, None otherwise.
        """
        from rich.console import Console
        console = Console()

        # ── License gate: coding team requires Pro ──
        if not self._check_pro_license(project_path, settings):
            console.print("[dim]Coding team: Pro license required — skipping[/dim]")
            return None

        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — can't create background task.
            # This happens in sync contexts; coding team won't run.
            console.print("[yellow]Coding team: no event loop — skipping[/yellow]")
            return None

        console.print("[bold green]Coding team[/bold green]: starting in background (Pro)")
        return loop.create_task(self._run_coding_team(project_path, storage, settings))

    async def _run_coding_team(self, project_path, storage, settings) -> None:
        """Full PM Agent workflow: fix → test → verify → accept → deploy.

        Runs as a background asyncio task alongside the analytics team.
        Loops indefinitely — one iteration every 60 seconds.
        """
        import time
        from rich.console import Console
        console = Console()

        from src.cli.commands_core import check_environment
        check_environment(project_path, require_redis=True, require_spec_editor_bin=True)

        self._ensure_agents_running(project_path)

        try:
            from spec_editor_cycle.engine import WorkflowEngine
            engine = WorkflowEngine(storage=storage, project_path=str(project_path), provider="opencode")

            while True:
                result = await engine.run(iterations=8)
                console.print(f"\n[bold]Coding workflow[/bold]: {result.get('status')}")
                console.print(f"  Bugs fixed: {result.get('bugs_fixed', 0)}")
                console.print(f"  Iterations: {result.get('iterations', 1)}")
                if result.get("status") != "error":
                    await asyncio.sleep(60)
                else:
                    break
        except ImportError as exc:
            console.print(f"[red]Workflow engine not available: {exc}[/red]")

    @staticmethod
    def _check_pro_license(project_path, settings) -> bool:
        """Verify Pro license before running cycle modes.

        Returns True if license is valid, False otherwise.
        Prints a helpful message and returns False on failure so the
        caller can abort gracefully.
        """
        from rich.console import Console
        console = Console()

        try:
            from src.licensing import create_license_provider
        except ImportError:
            # Licensing module not available — allow (OSS context)
            return True

        license_cfg = getattr(settings, 'license', None)
        if license_cfg is None or license_cfg.backend == "noop" or not license_cfg.key:
            console.print()
            console.print(
                "[red bold]Pro license required[/red bold]\n"
                "\n  Cycle, cycle-graph, coding, and team modes require "
                "a Spec Editor Pro license.\n"
                "\n  [dim]Get a license:[/dim] "
                "[cyan]https://gumroad.com/l/spec-editor-pro[/cyan]"
                "\n  [dim]Then activate:[/dim] "
                "[cyan]spec-editor license activate <key>[/cyan]\n"
            )
            return False

        import asyncio

        try:
            provider = create_license_provider(project_path, settings)
            status = asyncio.run(
                provider.validate_key(license_cfg.key, product="pro")
            )
            if not status.valid:
                console.print()
                console.print(
                    f"[red bold]License invalid[/red bold]\n"
                    f"\n  {status.message}"
                    f"\n  [dim]Get a license:[/dim] "
                    "[cyan]https://gumroad.com/l/spec-editor-pro[/cyan]\n"
                )
                return False
            return True
        except Exception as exc:
            console.print(
                f"[yellow]License check warning:[/yellow] {exc}\n"
                "[dim]Continuing — license validation unavailable.[/dim]"
            )
            return True  # Fail-open: don't block on transient errors

    def _run_ingest(self, project_path, storage) -> bool:
        """Ingest: read logs → detect bugs → push to Redis for PM Agent."""
        import asyncio
        from rich.console import Console
        console = Console()

        async def _run():
            from spec_editor_cycle.commands_cycle import _ingest_and_push
            return await _ingest_and_push(
                storage=storage, project_path=str(project_path),
                logs_path="logs/", modules=None, since="", dry_run=False,
            )

        result = asyncio.run(_run())
        console.print(f"[bold]Ingest complete[/bold]: {result.get('bugs_found', 0)} bug(s) found")
        if result.get("src_created"):
            console.print(f"  Bugs pushed to PM Agent via Redis: {result['src_created']}")
            console.print("  Run [bold]spec-editor run --mode cycle-graph[/bold] to start the PM Agent")
        return True

    def _run_pm_agent_workflow(self, project_path, storage, settings) -> bool:
        """Full PM Agent workflow: fix → test → verify → accept → deploy."""
        import asyncio
        import time
        from rich.console import Console
        console = Console()

        from src.cli.commands_core import check_environment
        check_environment(project_path, require_redis=True, require_spec_editor_bin=True)

        self._ensure_agents_running(project_path)

        try:
            from spec_editor_cycle.engine import WorkflowEngine
            engine = WorkflowEngine(storage=storage, project_path=str(project_path), provider="opencode")

            while True:
                result = asyncio.run(engine.run(iterations=8))
                console.print(f"\n[bold]Workflow[/bold]: {result.get('status')}")
                console.print(f"  Bugs fixed: {result.get('bugs_fixed', 0)}")
                console.print(f"  Iterations: {result.get('iterations', 1)}")
                if result.get("status") != "error":
                    time.sleep(60)
                else:
                    break
        except ImportError as exc:
            console.print(f"[red]Workflow engine not available: {exc}[/red]")
        return True

    def _run_coding(
        self, project_path, storage, method, agents_config, settings, initial_task
    ) -> bool:
        """Run Coding Agent in coding mode."""
        from rich.console import Console

        console = Console()

        from src.agents.langgraph_agent import LangGraphAgent
        from src.agents.role import AgentRole
        from src.agents.supervisor_graph import SupervisorGraph
        from src.agents.tools import build_all_handlers, get_tool_definitions
        from src.config.methodology import format_methodology
        from src.config.settings import create_provider
        from src.config.skills import SkillsRegistry
        from src.providers.base import LLMProvider

        skills_paths = []
        skills_dir = project_path / "skills"
        skills_file = project_path / "skills.yaml"
        if skills_dir.is_dir():
            skills_paths.append(skills_dir)
        if skills_file.exists():
            skills_paths.append(skills_file)

        code_skill = None
        if skills_paths:
            registry = SkillsRegistry(skills_paths)
            code_skill = registry.get("coding_agent")

        if code_skill is None:
            console.print("[red]coding_agent skill not found in skills/[/red]")
            raise SystemExit(1)

        role1 = AgentRole.from_skill(
            code_skill,
            writable=True,
            default_prompt="You are a Coding Agent. Generate code from spec.",
        )
        role1._allowed_tools = code_skill.tools
        role1.prompt = (
            "You are a Coding Agent. Your job is to generate working code "
            "from specification requirements.\n"
            "1. Read the target module via read_element.\n"
            "2. Read related entities, scenarios, NFRs.\n"
            "3. Generate code with @implements annotations.\n"
            "4. Add StructuredLogEmitter to every new module.\n"
            "5. Write tests, run them, fix failures.\n"
            "6. Verify with verify_implements.\n"
            "Follow TDD: tests first, then implementation."
        )
        console.print("[bold]Starting Coding Agent[/bold]")
        console.print(f"  Model: {agents_config.agent_1.model}")

        task_text = initial_task or (
            "Analyse the specification and produce a detailed implementation plan.\n\n"
            "1. Read all modules and related entities, scenarios, NFRs.\n"
            "2. Design the file structure.\n"
            "3. For each page/endpoint, list components and specify @implements.\n"
            "4. Output the plan as a structured document.\n"
            "Use list_all_elements, read_element, search_elements, get_context_for_file."
        )

        tools1 = get_tool_definitions(writable=role1.writable)
        if role1._allowed_tools:
            tools1 = [t for t in tools1 if t.name in role1._allowed_tools]
        handlers1 = build_all_handlers(storage, method, str(project_path / "source"))
        prompt1 = role1.prompt or ""

        def _provider_factory(agent_name: str) -> LLMProvider:
            if agent_name == "agent_1":
                return create_provider(agents_config.agent_1)
            return create_provider(agents_config.agent_2)

        graph = SupervisorGraph(
            storage=storage,
            config=agents_config,
            provider_factory=_provider_factory,
            agent1_prompt=prompt1,
            agent2_prompt="",
            agent1_tools=tools1,
            agent2_tools=[],
            agent1_handlers=handlers1,
            agent2_handlers={},
            max_llm_calls=settings.max_llm_calls,
            log_dir=project_path,
            project_path=project_path,
            source_dir=str(project_path / "source"),
        )

        async def _run():
            return await graph.run(task_text)

        result = asyncio.run(_run())
        console.print(
            f"\n[bold]Coding Agent finished: {result.get('status', 'unknown')}[/bold]"
        )
        return True

    # ── Agent worker management ────────────────────────────────────

    _REQUIRED_AGENTS: list[str] = [
        "coding",
        "tester",
        "project-manager",
        "analyst-manager",
        "devops",
    ]

    def _ensure_agents_running(self, project_path: Path) -> None:
        """Check that required agent workers are running with current code."""
        import os
        import signal
        import subprocess
        import time

        from src.agents.events import get_queue_url

        generation = str(int(time.time()))
        queue_url = get_queue_url(project_path)
        redis_client = None
        if "redis" in queue_url:
            try:
                from src.agents.events import ensure_redis_available

                ensure_redis_available(project_path)
                import redis

                redis_client = redis.from_url(
                    queue_url.split("?")[0], socket_connect_timeout=2
                )
                redis_client.ping()

                prefix = ""
                if "prefix=" in queue_url:
                    from urllib.parse import parse_qs, urlparse

                    params = parse_qs(urlparse(queue_url).query)
                    prefixes = params.get("prefix", [])
                    if prefixes:
                        prefix = prefixes[0] + ":"

                gen_key = f"{prefix}agent-generation"
                redis_client.set(gen_key, generation)
            except Exception:
                redis_client = None

        # Kill stale agents...
        project_str = str(project_path)
        our_pid = str(os.getpid())
        try:
            result = subprocess.run(
                ["ps", "-eo", "pid,args"], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if "grep" in line:
                    continue
                parts = line.split(None, 1)
                if not parts:
                    continue
                if parts[0] == our_pid:
                    continue
                if (
                    "cycle-graph" in line
                    and "--watch" in line
                    and f"-p {project_str}" in line
                ):
                    try:
                        os.kill(int(parts[0]), signal.SIGTERM)
                        time.sleep(0.5)
                    except Exception:
                        pass
        except Exception:
            pass

        # Start missing agents
        running: dict[str, list[str]] = {}
        try:
            result = subprocess.run(
                ["ps", "-eo", "pid,args"], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if "grep" in line:
                    continue
                for role in self._REQUIRED_AGENTS:
                    if f"agent {role}" in line and "--watch" in line:
                        running.setdefault(role, []).append(line)
        except Exception:
            pass

        from rich.console import Console

        console = Console()

        # Resolve spec-editor binary from current venv
        import sys as _sys
        spec_editor_bin = str(Path(_sys.executable).parent / "spec-editor")

        # ── 1. Start one instance of each required agent role ──
        missing = [r for r in self._REQUIRED_AGENTS if r not in running]
        if missing:
            console.print(f"[bold]Starting agents:[/bold] {', '.join(missing)}")
            for role in missing:
                self._spawn_agent(spec_editor_bin, role, project_path, console)

        # ── 2. Scale coding workers based on queue depth ──
        max_coding_workers = 5
        coding_workers = len(running.get("coding", []))
        coding_queue_depth = 0
        if redis_client:
            try:
                prefix = self._get_redis_prefix(queue_url)
                coding_queue_depth = redis_client.xlen(f"{prefix}tasks:coding")
            except Exception:
                pass

        needed = min(coding_queue_depth, max_coding_workers) - coding_workers
        for i in range(needed):
            label = f"coding#{coding_workers + i + 1}"
            self._spawn_agent(spec_editor_bin, "coding", project_path, console, label=label)

        if needed > 0:
            console.print(
                f"  [dim]Queue depth={coding_queue_depth}, "
                f"workers={coding_workers}→{coding_workers + needed}[/dim]"
            )

        if redis_client:
            redis_client.close()

        if not missing and needed <= 0:
            console.print("[dim]All agent workers up-to-date[/dim]")
        console.print()

    @staticmethod
    def _get_redis_prefix(queue_url: str) -> str:
        """Extract prefix from Redis queue URL."""
        from urllib.parse import parse_qs, urlparse

        params = parse_qs(urlparse(queue_url).query)
        prefixes = params.get("prefix", [])
        return prefixes[0] + ":" if prefixes else ""

    @staticmethod
    def _spawn_agent(
        spec_editor_bin: str,
        role: str,
        project_path: Path,
        console,
        label: str | None = None,
    ) -> None:
        """Spawn a single agent worker subprocess."""
        import subprocess
        import time

        display = label or role
        log_suffix = f"-{label}" if label else ""
        agent_log = project_path / "logs" / f"agent-{role}{log_suffix}.log"
        agent_log.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(agent_log, "a") as log_f:
                log_f.write(
                    f"\n--- Agent {display} started at "
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')} ---\n"
                )
                proc = subprocess.Popen(
                    [
                        spec_editor_bin,
                        "agent",
                        role,
                        "--watch",
                        "-p",
                        str(project_path),
                    ],
                    stdout=log_f,
                    stderr=log_f,
                    start_new_session=True,
                )
            console.print(f"  [green]Started[/green] {display} (PID {proc.pid})")
        except Exception as exc:
            console.print(f"  [red]Failed[/red] {display}: {exc}")

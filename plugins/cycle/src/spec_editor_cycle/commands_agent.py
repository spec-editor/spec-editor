"""CLI command: ``spec-editor agent`` — persistent agent workers.

Usage::

    spec-editor agent coding --watch      # coding agent on task queue
    spec-editor agent tester --watch      # QA/tester agent
    spec-editor agent devops --watch      # DevOps agent

    spec-editor agent coding --once       # one-shot (process one task, exit)

    # Use Redis:
    SPEC_EDITOR_QUEUE_URL=redis://localhost:6379 spec-editor agent coding --watch

This module is imported by the cycle plugin's cli.py.
Commands are added to the Click group via ``cli_group.add_command()``,
not via module-level decorators on ``cli``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import click
from rich.console import Console

console = Console()


@click.group("agent")
def agent_group():
    """Manage persistent AI agents."""
    pass


@agent_group.command("status")
@click.option(
    "-p", "--project", "project_path", default=None, help="Path to spec-editor project."
)
def status(project_path: str | None):
    """Show running agents, queue, token usage, and current element per agent."""
    import json as _json
    import re as _re
    import subprocess
    from pathlib import Path

    from src.agents.task_queue import get_queue_url

    if project_path:
        proj = Path(project_path).resolve()
    else:
        cwd = Path.cwd()
        if (cwd / "methodology.yaml").exists():
            proj = cwd
        else:
            proj = None

    # ── Running agent processes ──
    console.print("\n[bold]Running agents:[/bold]")
    try:
        result = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=5
        )

        # ── Detect cycle process via lock file ──
        lock_path = proj / ".spec-editor-cycle.lock" if proj else None
        cycle_pid = None
        if lock_path and lock_path.exists():
            try:
                cycle_pid = int(lock_path.read_text().strip())
                import os as _os
                _os.kill(cycle_pid, 0)  # check if alive
            except Exception:
                cycle_pid = None

        if cycle_pid:
            console.print(
                f"  [green]●[/green] {'cycle (all agents)':20s}  PID={cycle_pid}"
                f"  [dim](coding, project-manager, analyst-manager, tester, devops)[/dim]"
            )
        # Fall through to ps scanning for standalone agents too

        # Patterns: persistent workers + PM/spec agents
        patterns = [
            ("agent", "watch"),  # persistent workers (standalone)
            ("spec-editor run", ""),  # cycle-graph workflow
            ("opencode", "run"),  # OpenCode coding agent
        ]
        found_roles = {}
        for line in result.stdout.splitlines():
            if "grep" in line:
                continue
            for keyword, sub in patterns:
                if keyword in line and (not sub or sub in line):
                    parts = line.split()
                    pid = parts[1]
                    cpu = parts[2] + "%"
                    mem = parts[3] + "%"
                    if keyword == "agent":
                        role = "unknown"
                        for i, p in enumerate(parts):
                            if p == "agent":
                                role = parts[i + 1] if i + 1 < len(parts) else "?"
                                break
                    elif keyword == "spec-editor run":
                        role = "PM-agent"
                    elif keyword == "opencode":
                        role = "coding (opencode)"
                    else:
                        role = keyword
                    if role not in found_roles:
                        found_roles[role] = (pid, cpu, mem)
        # Deduplicate by PID (same process should not appear twice)
        seen_pids = set()
        unique_roles = {}
        for role, (pid, cpu, mem) in sorted(found_roles.items()):
            if pid in seen_pids:
                continue
            seen_pids.add(pid)
            unique_roles[role] = (pid, cpu, mem)

        # Detect sub-process relationships: "coding (opencode)" is a
        # sub-process of "coding" agent worker
        sub_of: dict[str, str] = {}
        for role in unique_roles:
            if " (opencode)" in role:
                base = role.replace(" (opencode)", "")
                if base in unique_roles:
                    sub_of[role] = base

        if unique_roles:
            # ── Resolve current element for each coding agent ──
            current_elements = _get_current_elements(proj)

            for role, (pid, cpu, mem) in sorted(unique_roles.items()):
                element_info = ""
                # Match both "coding" and "coding#N"
                el_id, el_title = "", ""
                if role in current_elements:
                    el_id, el_title = current_elements[role]
                elif role == "coding":
                    # Try coding#1, coding#2, etc.
                    for i in range(1, 10):
                        key = f"coding#{i}"
                        if key in current_elements:
                            el_id, el_title = current_elements.pop(key)
                            break
                if el_id:
                    element_info = f"  [bold cyan]{el_id}[/bold cyan] [dim]{el_title[:50]}[/dim]"

                if role in sub_of:
                    parent_pid = unique_roles[sub_of[role]][0]
                    console.print(
                        f"  [green]●[/green] {role:20s}  PID={pid}  CPU={cpu}  MEM={mem}"
                        f"  [dim](subprocess of {sub_of[role]} PID={parent_pid})[/dim]"
                        f"{element_info}"
                    )
                else:
                    console.print(
                        f"  [green]●[/green] {role:20s}  PID={pid}  CPU={cpu}  MEM={mem}"
                        f"{element_info}"
                    )

            # Show remaining active elements (extra coding agents beyond ps-visible ones)
            for key, (el_id, el_title) in sorted(current_elements.items()):
                console.print(
                    f"  [green]●[/green] {key:20s}  PID=?  CPU=?  MEM=?"
                    f"  [bold cyan]{el_id}[/bold cyan] [dim]{el_title[:50]}[/dim]"
                    f"  [dim](active, no ps entry)[/dim]"
                )
        elif not cycle_pid:
            console.print("  [dim](no agents running)[/dim]")
    except Exception:
        console.print("  [dim](cannot check processes)[/dim]")

    # ── Queue + Usage ──
    if proj:
        queue_url = get_queue_url(proj)
        console.print(f"\n[bold]Queue:[/bold] {queue_url}")
        tasks_dir = proj / "tasks"
        if tasks_dir.is_dir():
            for role_dir in sorted(tasks_dir.iterdir()):
                if not role_dir.is_dir():
                    continue
                role = role_dir.name
                pending = (
                    len(list((role_dir / "pending").glob("*.json")))
                    if (role_dir / "pending").is_dir()
                    else 0
                )
                in_progress = (
                    len(list((role_dir / "in_progress").glob("*.json")))
                    if (role_dir / "in_progress").is_dir()
                    else 0
                )
                done = (
                    len(list((role_dir / "done").glob("*.result.json")))
                    if (role_dir / "done").is_dir()
                    else 0
                )

                # Usage
                usage_file = role_dir / "usage.json"
                usage_str = ""
                if usage_file.exists():
                    try:
                        u = _json.loads(usage_file.read_text())
                        tin = u.get("tokens_in", 0)
                        tout = u.get("tokens_out", 0)
                        tasks = u.get("tasks_done", 0)
                        # DeepSeek pricing: $0.27/M in, $1.10/M out (reasoner)
                        cost = (tin / 1_000_000) * 0.27 + (tout / 1_000_000) * 1.10
                        usage_str = f"  [dim]tk_in={tin} tk_out={tout} tasks={tasks} ~${cost:.4f}[/dim]"
                    except Exception:
                        pass

                console.print(
                    f"  {role:10s}  [yellow]pending={pending}[/yellow]  "
                    f"[blue]in_progress={in_progress}[/blue]  [green]done={done}[/green]"
                    f"{usage_str}"
                )
        else:
            console.print("  [dim](no tasks directory)[/dim]")
    else:
        console.print("\n[dim]No project. Use -p.[/dim]")


@agent_group.command("coding")
@click.option(
    "-p", "--project", "project_path", default=None, help="Path to spec-editor project."
)
@click.option(
    "--watch/--once",
    default=True,
    help="Watch mode (persistent) or one-shot. DEPRECATED: use orchestrator.",
)
@click.option(
    "--queue",
    "queue_url",
    default="",
    help="Queue URL (redis://, file://). Auto-detected if empty.",
)
def coding(project_path: str | None, watch: bool, queue_url: str):
    """Coding agent — listens on task queue, fixes bugs via OpenCode."""
    _run_agent("coding", project_path, watch, queue_url)


@agent_group.command("tester")
@click.option(
    "-p", "--project", "project_path", default=None, help="Path to spec-editor project."
)
@click.option(
    "--watch/--once",
    default=True,
    help="Watch mode (persistent) or one-shot. DEPRECATED: use orchestrator.",
)
@click.option(
    "--queue",
    "queue_url",
    default="",
    help="Queue URL (redis://, file://). Auto-detected if empty.",
)
def tester(project_path: str | None, watch: bool, queue_url: str):
    """QA/tester agent — semantic spec-to-code verification."""
    _run_agent("tester", project_path, watch, queue_url)


@agent_group.command("project-manager")
@click.option(
    "-p", "--project", "project_path", default=None, help="Path to spec-editor project."
)
@click.option(
    "--watch/--once", default=True, help="Watch mode (persistent) or one-shot."
)
@click.option(
    "--queue",
    "queue_url",
    default="",
    help="Queue URL (redis://, file://). Auto-detected if empty.",
)
def project_manager_cmd(project_path: str | None, watch: bool, queue_url: str):
    """Project Manager — coordinates Agent1+Agent2 to refine requirements (coding cycle)."""
    _run_agent("project-manager", project_path, watch, queue_url)


@agent_group.command("analyst-manager")
@click.option(
    "-p", "--project", "project_path", default=None, help="Path to spec-editor project."
)
@click.option(
    "--watch/--once",
    default=True,
    help="Watch mode (persistent) or one-shot.",
)
@click.option(
    "--queue",
    "queue_url",
    default="",
    help="Queue URL (redis://, file://). Auto-detected if empty.",
)
def analyst_manager_cmd(project_path: str | None, watch: bool, queue_url: str):
    """Analyst Manager — receives spec:refine events, spawns analyst agents."""
    _run_agent("analyst-manager", project_path, watch, queue_url)


@agent_group.command("spec-update")
@click.option(
    "-p", "--project", "project_path", default=None, help="Path to spec-editor project."
)
@click.option(
    "--watch/--once",
    default=True,
    help="Watch mode (persistent) or one-shot. DEPRECATED: use orchestrator.",
)
@click.option(
    "--queue",
    "queue_url",
    default="",
    help="Queue URL (redis://, file://). Auto-detected if empty.",
)
def spec_update(project_path: str | None, watch: bool, queue_url: str):
    """Spec updater agent — details vague requirements (Agent1/2 role)."""
    _run_agent("spec_update", project_path, watch, queue_url)


@agent_group.command("devops")
@click.option(
    "-p", "--project", "project_path", default=None, help="Path to spec-editor project."
)
@click.option(
    "--watch/--once",
    default=True,
    help="Watch mode (persistent) or one-shot. DEPRECATED: use orchestrator.",
)
@click.option(
    "--queue",
    "queue_url",
    default="",
    help="Queue URL (redis://, file://). Auto-detected if empty.",
)
def devops(project_path: str | None, watch: bool, queue_url: str):
    """DevOps agent — build, deploy, verify."""
    _run_agent("devops", project_path, watch, queue_url)


@agent_group.command("refactor")
@click.option(
    "-p", "--project", "project_path", default=None, help="Path to spec-editor project."
)
@click.option(
    "--target-dir",
    "-d",
    multiple=True,
    default=["src/"],
    help="Directories to refactor (can be repeated). Default: src/",
)
@click.option(
    "--target-file",
    "-f",
    "target_files",
    multiple=True,
    default=None,
    help="Specific files to refactor (can be repeated).",
)
@click.option(
    "--type",
    "refactor_type",
    default="general",
    type=click.Choice([
        "general", "duplication", "complexity", "naming",
        "dead_code", "error_handling", "god_class", "magic_numbers",
    ]),
    help="Type of refactoring to perform.",
)
@click.option(
    "--task",
    "task_description",
    default="",
    help="Custom refactoring task description (overrides --type).",
)
@click.option(
    "--model",
    default="",
    help="LLM model override. Default: reads from agents.yaml or env.",
)
@click.option(
    "--watch/--once",
    default=False,
    help="Watch mode (persistent) or one-shot. Default: one-shot.",
)
@click.option(
    "--queue",
    "queue_url",
    default="",
    help="Queue URL (redis://, file://). Auto-detected if empty.",
)
def refactor_cmd(
    project_path: str | None,
    target_dir: tuple[str, ...],
    target_files: tuple[str, ...],
    refactor_type: str,
    task_description: str,
    model: str,
    watch: bool,
    queue_url: str,
):
    """Refactoring agent — improve code structure without changing behaviour.

    \b
    Analyses code for smells (duplication, complexity, dead code, etc.)
    and applies safe transformations. Runs tests before and after —
    every existing test MUST stay green.

    \b
    Examples:
      spec-editor agent refactor -p . -d src/
      spec-editor agent refactor -p . -d src/ --type duplication
      spec-editor agent refactor -p . -f src/app.py --type complexity
      spec-editor agent refactor -p . -d src/ --task "Extract auth helpers"
    """
    from pathlib import Path

    from src.agents.persistent_agent import AgentWorker

    if project_path:
        proj = Path(project_path).resolve()
    else:
        cwd = Path.cwd()
        if (cwd / "methodology.yaml").exists():
            proj = cwd
        else:
            console.print(
                "[red]Error:[/red] No spec-editor project found. "
                "Run from a project directory or use -p."
            )
            raise SystemExit(1)

    console.print(f"[bold]Refactor[/bold]  |  Project: {proj}")
    console.print(f"[dim]Target dirs: {list(target_dir) if target_dir else 'src/'}[/dim]")
    if target_files:
        console.print(f"[dim]Target files: {list(target_files)}[/dim]")
    console.print(f"[dim]Type: {refactor_type}[/dim]")
    if task_description:
        console.print(f"[dim]Custom task: {task_description[:80]}...[/dim]")

    worker = AgentWorker(role="refactor", project_path=proj, queue_url=queue_url)

    # Build task payload
    task_payload = {
        "target_dirs": list(target_dir) if target_dir else ["src/"],
        "target_files": list(target_files) if target_files else [],
        "refactor_type": refactor_type,
        "task": task_description,
    }
    if model:
        task_payload["model"] = model

    if watch:
        console.print(f"[green]Watching for tasks...[/green] (Ctrl+C to stop)")
        try:
            asyncio.run(worker.run())
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopped.[/yellow]")
    else:
        async def _once():
            from src.agents.task_queue import Task

            import time as _time
            task = Task(
                task_id=f"refactor-{int(_time.time())}",
                role="refactor",
                payload=task_payload,
            )
            console.print(f"[dim]Task: {task.task_id}[/dim]")
            result = await worker.handle_task(task)
            console.print(f"[green]Done: {result.status}[/green]")
            if result.payload:
                summary = result.payload
                files = summary.get("files_changed", [])
                tests = summary.get("tests_pass", False)
                lint = summary.get("lint_issues", 0)
                console.print(f"  Files changed: {len(files)}")
                console.print(f"  Tests passing: {'✓' if tests else '✗'}")
                console.print(f"  Lint issues: {lint}")

        asyncio.run(_once())


@agent_group.command("reengineer")
@click.option(
    "-p", "--project", "project_path", default=None, help="Path to spec-editor project."
)
@click.option(
    "--code-dir",
    "-c",
    default=None,
    type=click.Path(exists=True),
    help="Directory containing the codebase to reverse-engineer (default: project root).",
)
@click.option(
    "--deep",
    is_flag=True,
    help="Enable Phase 3: behaviour tracing (state machines, scenarios).",
)
@click.option(
    "--watch/--once",
    default=False,
    help="Watch mode (persistent) or one-shot. Default: one-shot.",
)
@click.option(
    "--queue",
    "queue_url",
    default="",
    help="Queue URL (redis://, file://). Auto-detected if empty.",
)
def reengineer_cmd(
    project_path: str | None,
    code_dir: str | None,
    deep: bool,
    watch: bool,
    queue_url: str,
):
    """Reverse-engineer existing codebase into specification elements.

    \b
    Analyses project structure, APIs, UI components, and build infrastructure.
    Creates implementation and module elements with status=confirmed.
    With --deep: also traces behaviours and user scenarios.

    \b
    Examples:
      spec-editor cycle reengineer -p . -c ./src
      spec-editor cycle reengineer -p . -c ./src --deep
    """
    _run_reengineer(project_path, code_dir, deep, watch, queue_url)


def _run_reengineer(
    project_path: str | None,
    code_dir: str | None,
    deep: bool,
    watch: bool,
    queue_url: str,
) -> None:
    """Run the reengineer agent to reverse-engineer a codebase into specs."""
    from pathlib import Path

    from src.agents.persistent_agent import AgentWorker

    if project_path:
        proj = Path(project_path).resolve()
    else:
        cwd = Path.cwd()
        if (cwd / "methodology.yaml").exists():
            proj = cwd
        else:
            console.print(
                "[red]Error:[/red] No spec-editor project found. "
                "Run from a project directory or use -p."
            )
            raise SystemExit(1)

    code = Path(code_dir).resolve() if code_dir else proj
    if not code.is_dir():
        console.print(f"[red]Error:[/red] Code directory not found: {code}")
        raise SystemExit(1)

    console.print(f"[bold]Reengineer[/bold]  |  Project: {proj}  |  Code: {code}")
    if deep:
        console.print("[dim]Mode: deep (Phase 3: behaviour tracing enabled)[/dim]")
    else:
        console.print("[dim]Mode: standard (Phases 1-2)[/dim]")

    worker = AgentWorker(role="reengineer", project_path=proj, queue_url=queue_url)

    # Build task for the reengineer
    task_payload = {
        "code_dir": str(code),
        "deep": deep,
        "phases": ["structure", "devops", "api", "ui"] + (["behaviour"] if deep else []),
    }

    if watch:
        console.print(f"[green]Watching for tasks...[/green] (Ctrl+C to stop)")
        try:
            asyncio.run(worker.run())
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopped.[/yellow]")
    else:
        async def _once():
            from src.agents.task_queue import Task

            import time as _time
            task = Task(
                task_id=f"reengineer-{int(_time.time())}",
                role="reengineer",
                payload=task_payload,
            )
            console.print(f"[dim]Task: {task.task_id}[/dim]")
            result = await worker.handle_task(task)
            console.print(f"[green]Done: {result.status}[/green]")
            if result.payload:
                summary = result.payload.get("summary", "")
                if summary:
                    console.print(summary)

        asyncio.run(_once())


def _run_agent(
    role: str, project_path: str | None, watch: bool, queue_url: str
) -> None:
    """Run an agent worker."""
    from src.agents.persistent_agent import AgentWorker

    if project_path:
        proj = Path(project_path).resolve()
    else:
        cwd = Path.cwd()
        if (cwd / "methodology.yaml").exists():
            proj = cwd
        else:
            console.print(
                "[red]Error:[/red] No spec-editor project found. "
                "Run from a project directory or use -p."
            )
            raise SystemExit(1)

    console.print(f"[bold]Agent: {role}[/bold]  |  Project: {proj}")
    console.print(f"[dim]Queue: {queue_url or 'auto-detect'}[/dim]")

    worker = AgentWorker(role=role, project_path=proj, queue_url=queue_url)

    if watch:
        console.print(f"[green]Watching for tasks...[/green] (Ctrl+C to stop)")
        try:
            asyncio.run(worker.run())
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopped.[/yellow]")
    else:
        # One-shot: process one pending task if any
        async def _once():
            from src.agents.task_queue import AbstractTaskQueue, get_queue_url

            q = AbstractTaskQueue.connect(queue_url or get_queue_url(proj))
            await q.connect()
            pending = await q.pending(role)
            if not pending:
                console.print("[dim]No pending tasks.[/dim]")
                return
            task = pending[0]
            console.print(f"Processing: {task.task_id}")
            result = await worker.handle_task(task)
            await q.ack(task, result)
            console.print(f"[green]Done: {result.status}[/green]")

        asyncio.run(_once())


def _get_current_elements(proj: Path | None) -> dict[str, tuple[str, str]]:
    """Find which element each coding agent is working on.

    Strategy: elements with 'attempts:0' tag that have NO corresponding
    task in the Redis queue are being actively processed by a coding agent.

    Returns dict: agent_name → (element_id, element_title).
    """
    if not proj:
        return {}

    current: dict[str, tuple[str, str]] = {}

    try:
        from src.agents.events import get_queue_url
        from src.storage.filesystem import FilesystemStorage

        storage = FilesystemStorage(proj)
        queue_url = get_queue_url(proj)

        # Get dispatched-but-not-in-queue elements
        dispatched: list[str] = []
        for e in storage.list_all():
            tags = getattr(e, "tags", []) or []
            if any(t.startswith("attempts:") for t in tags):
                dispatched.append(e.id)

        if not dispatched:
            return {}

        # Check which are still in Redis queue
        import redis
        from urllib.parse import parse_qs, urlparse

        redis_client = redis.from_url(queue_url.split("?")[0], socket_connect_timeout=2)
        prefix = ""
        if "prefix=" in queue_url:
            params = parse_qs(urlparse(queue_url).query)
            prefixes = params.get("prefix", [])
            if prefixes:
                prefix = prefixes[0] + ":"

        coding_key = f"{prefix}tasks:coding"
        in_queue: set[str] = set()

        # Read all pending tasks from stream
        try:
            import json as _json
            stream_entries = redis_client.xrange(coding_key, "-", "+")
            for entry_id, fields in stream_entries:
                bug_id = ""
                # fields is list of key-value pairs: [k1, v1, k2, v2, ...]
                if isinstance(fields, list):
                    d = {}
                    for i in range(0, len(fields), 2):
                        k = fields[i].decode() if isinstance(fields[i], bytes) else str(fields[i])
                        v = fields[i+1].decode() if isinstance(fields[i+1], bytes) else str(fields[i+1])
                        d[k] = v
                    # bug_id is inside the JSON-encoded 'payload' field
                    payload_raw = d.get("payload", "{}")
                    try:
                        payload = _json.loads(payload_raw)
                        bug_id = payload.get("bug_id", "")
                    except Exception:
                        pass
                if bug_id:
                    in_queue.add(bug_id)
        except Exception:
            pass

        # Elements that are dispatched but NOT in queue = being processed
        active = [eid for eid in dispatched if eid not in in_queue]

        # Fallback: if no tasks in stream but elements are dispatched,
        # the agents have consumed them. Show them as active.
        if not active and dispatched:
            # Check if OpenCode is running (indicates active processing)
            import subprocess as _sp
            try:
                oc_result = _sp.run(["pgrep", "-fl", "opencode"], capture_output=True, text=True)
                opencode_running = bool(oc_result.stdout.strip())
            except Exception:
                opencode_running = False

            if opencode_running or not in_queue:
                # Either OpenCode is running, or stream was emptied
                active = dispatched[:]

        # Map to coding agents (round-robin assignment)
        if active:
            # Count coding agent processes
            import subprocess as _sp2
            result = _sp2.run(["pgrep", "-f", "agent coding.*--watch"], capture_output=True, text=True)
            coding_pids = [p for p in result.stdout.strip().split("\n") if p]
            num_agents = max(len(coding_pids), 1)

            for i, eid in enumerate(active):
                agent_label = "coding" if num_agents <= 1 else f"coding#{i % num_agents + 1}"
                try:
                    el = storage.read_element(eid)
                    current[agent_label] = (eid, el.title)
                except Exception:
                    current[agent_label] = (eid, "")

        try:
            redis_client.close()
        except Exception:
            pass
    except Exception:
        pass

    return current

"""CLI command: ``spec-editor shutdown`` — graceful stop of all agents and subprocesses.

Usage::

    spec-editor shutdown              # stop all spec-editor processes
    spec-editor shutdown --docker     # also stop Docker containers
    spec-editor shutdown --force      # SIGKILL instead of SIGTERM
    spec-editor shutdown --dry-run    # show what would be stopped
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from src.cli.commands import cli

console = Console()

# ── Ordered list of process patterns to stop (dependencies first) ──
_SHUTDOWN_ORDER: list[tuple[str, str, str]] = [
    # (grep pattern, display name, description)
    ("opencode run", "OpenCode subprocess", "Coding agent external tool"),
    ("spec-editor agent coding", "Coding agent worker", "Persistent coding worker"),
    ("src.main agent coding", "Coding agent worker (legacy)", "Legacy coding worker"),
    ("spec-editor agent tester", "Tester agent worker", "QA/tester worker"),
    ("src.main agent tester", "Tester agent worker (legacy)", "Legacy tester worker"),
    (
        "spec-editor agent analyst-manager",
        "Analyst manager worker",
        "Analyst manager — spec refinement events",
    ),
    ("spec-editor agent project-manager", "PM agent worker", "Project manager worker"),
    ("src.main agent project-manager", "PM agent worker (legacy)", "Legacy PM worker"),
    ("spec-editor agent devops", "DevOps agent worker", "Deployment worker"),
    ("src.main agent devops", "DevOps agent worker (legacy)", "Legacy devops worker"),
    ("spec-editor agent spec_update", "Spec updater worker", "Spec refinement worker"),
    (
        "src.main agent spec_update",
        "Spec updater worker (legacy)",
        "Legacy spec updater",
    ),
    ("spec-editor run", "Workflow engine", "spec-editor run cycle"),
    ("src.main run", "Workflow engine (legacy)", "Legacy run cycle"),
    ("spec-editor mcp", "MCP server", "HTTP MCP server"),
    ("src.main mcp", "MCP server (legacy)", "Legacy MCP server"),
]


def _find_spec_processes() -> list[dict]:
    """Find all running spec-editor processes. Returns list of {pid, cmd, name}."""
    try:
        result = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=5
        )
    except Exception:
        return []

    processes: list[dict] = []
    seen_pids: set[str] = set()

    for line in result.stdout.splitlines():
        if "grep" in line:
            continue
        parts = line.split()
        if len(parts) < 11:
            continue
        pid = parts[1]
        if pid in seen_pids:
            continue

        cmd = " ".join(parts[10:])
        for pattern, name, _desc in _SHUTDOWN_ORDER:
            if pattern in cmd:
                seen_pids.add(pid)
                processes.append({"pid": pid, "cmd": cmd[:120], "name": name})
                break

    return processes


def _stop_process(pid: str, force: bool = False, dry_run: bool = False) -> str:
    """Stop a process by PID. Returns status string (cross-platform)."""
    from src.utils import terminate_process

    action = "force kill" if force else "terminate"

    if dry_run:
        return f"[dim]would {action}[/dim]"

    try:
        pid_int = int(pid)
    except ValueError:
        return f"[red]invalid PID: {pid}[/red]"

    if terminate_process(pid_int):
        return f"[green]{action} ✓[/green]"

    # Process may have already exited
    from src.utils import is_process_running
    if not is_process_running(pid_int):
        return "[dim]already gone[/dim]"
    return "[red]failed[/red]"


def _cleanup_files(project_path: str | None = None, dry_run: bool = False) -> None:
    """Remove lock files and temp artifacts."""
    paths_to_check = []
    if project_path:
        paths_to_check.append(Path(project_path))
    else:
        # Check common locations
        cwd = Path.cwd()
        for candidate in [cwd, cwd.parent, cwd.parent.parent]:
            if (candidate / "local.yaml").exists():
                paths_to_check.append(candidate)
                break
        if not paths_to_check:
            paths_to_check.append(cwd)

    for proj in paths_to_check:
        lock_file = proj / ".spec-editor-running"
        if lock_file.exists():
            if dry_run:
                console.print(f"  [dim]would remove {lock_file}[/dim]")
            else:
                lock_file.unlink()
                console.print(f"  [green]Removed[/green] {lock_file}")


def _stop_docker(dry_run: bool = False) -> None:
    """Stop spec-editor Docker containers."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=spec-editor", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        containers = [c.strip() for c in result.stdout.splitlines() if c.strip()]
        if not containers:
            console.print("  [dim]No spec-editor Docker containers found[/dim]")
            return

        for container in containers:
            if dry_run:
                console.print(f"  [dim]would stop container {container}[/dim]")
            else:
                subprocess.run(
                    ["docker", "stop", container],
                    capture_output=True,
                    timeout=10,
                )
                console.print(f"  [green]Stopped[/green] container {container}")
    except Exception as exc:
        console.print(f"  [yellow]Docker not available: {exc}[/yellow]")


@cli.command("shutdown")
@click.option(
    "--docker/--no-docker",
    default=False,
    help="Also stop Docker containers (spec-editor-mcp)",
)
@click.option(
    "--force",
    is_flag=True,
    help="Use SIGKILL instead of SIGTERM",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be stopped without actually stopping",
)
def shutdown(docker: bool, force: bool, dry_run: bool):
    """Gracefully stop all spec-editor agents, workers, and subprocesses.

    Stops in dependency order: OpenCode → workers → engine → MCP.
    Use before upgrading spec-editor or as part of CI teardown.
    """

    if dry_run:
        console.print(
            "[bold yellow]DRY RUN — no processes will be stopped[/bold yellow]\n"
        )
    elif force:
        console.print("[bold red]FORCE mode — using SIGKILL[/bold red]\n")
    else:
        console.print("[bold]Shutting down spec-editor...[/bold]\n")

    # ── 1. Find and stop processes ──
    processes = _find_spec_processes()

    if not processes:
        console.print("  [dim]No spec-editor processes running[/dim]")
    else:
        table = Table(title="Stopping processes")
        table.add_column("PID", style="cyan")
        table.add_column("Component", style="bold")
        table.add_column("Status")

        for proc in processes:
            status = _stop_process(proc["pid"], force=force, dry_run=dry_run)
            table.add_row(proc["pid"], proc["name"], status)

        console.print(table)

    # ── 2. Wait for processes to terminate ──
    if processes and not dry_run:
        console.print("\n  Waiting for processes to terminate...")
        time.sleep(2)

        remaining = _find_spec_processes()
        if remaining:
            console.print(
                f"  [yellow]{len(remaining)} process(es) still running "
                f"after SIGTERM[/yellow]"
            )
            for proc in remaining:
                console.print(f"    PID {proc['pid']}: {proc['name']}")
            if not force:
                console.print("  [dim]Use --force for SIGKILL if needed[/dim]")
        else:
            console.print("  [green]All processes stopped ✓[/green]")

    # ── 3. Clean up lock files ──
    console.print("\n[bold]Cleanup:[/bold]")
    _cleanup_files(dry_run=dry_run)

    # ── 4. Stop Docker containers ──
    if docker:
        console.print("\n[bold]Docker:[/bold]")
        _stop_docker(dry_run=dry_run)

    console.print("\n[bold green]Shutdown complete.[/bold green]")

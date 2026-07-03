"""CLI subcommand."""

import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from src.cli.commands import _BUILTIN_METHODOLOGIES, cli, console
from src.config.methodology import load_methodology
from src.mcp.metrics import compute_metrics
from src.mcp.validator import ValidationReport, validate
from src.storage.filesystem import FilesystemStorage

# ======================================================================
# check_environment — shared pre-flight validation
# ======================================================================


def check_environment(
    project_path: Path,
    *,
    require_redis: bool = False,
    require_spec_editor_bin: bool = True,
) -> bool:
    """Validate the environment before running commands.

    Checks that critical dependencies are available.  Prints ERROR for
    critical problems and WARN for non-critical ones.  Returns True if
    safe to proceed, False if ``SystemExit`` has already been raised.

    Callers that need different criticality can catch SystemExit and
    convert to a warning.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # ── 1. spec-editor binary (needed to spawn agent subprocesses) ──
    if require_spec_editor_bin:
        spec_bin = Path(sys.executable).parent / "spec-editor"
        if not spec_bin.is_file():
            errors.append(
                f"spec-editor binary not found at {spec_bin} — "
                f"agent subprocesses cannot be spawned"
            )
        elif not os.access(spec_bin, os.X_OK):
            errors.append(
                f"spec-editor at {spec_bin} is not executable"
            )

    # ── 2. Redis connectivity ──
    redis_ok = False
    try:
        import redis

        r = redis.from_url("redis://127.0.0.1:6379", socket_connect_timeout=2)
        r.ping()
        r.close()
        redis_ok = True
    except Exception:
        pass

    if require_redis and not redis_ok:
        errors.append("Redis is not reachable at 127.0.0.1:6379 — agent task queues won't work")
    elif not redis_ok:
        warnings.append("Redis is not reachable at 127.0.0.1:6379 — agent task queues won't work")

    # ── 3. methodology.yaml ──
    method_path = project_path / "methodology.yaml"
    if not method_path.exists():
        warnings.append(f"methodology.yaml not found in {project_path}")

    # ── Report ──
    section = bool(errors) or bool(warnings)
    if section:
        console.print()
        console.print("[bold]Environment check[/bold]")

    for msg in errors:
        console.print(f"  [red]ERROR[/red]  {msg}")
    for msg in warnings:
        console.print(f"  [yellow]WARN[/yellow]   {msg}")

    if errors:
        console.print()
        console.print("[red]Critical errors found — cannot continue.[/red]")
        raise SystemExit(1)

    if section:
        console.print()

    return True


@cli.command()
@click.option(
    "--path",
    "-p",
    default=".",
    envvar="SPEC_EDITOR_PROJECT",
    type=click.Path(exists=True),
    help="Path to project (or SPEC_EDITOR_PROJECT)",
)
@click.option("--strict", is_flag=True, help="Strict mode (warnings → errors)")
def validate_cmd(path: str, strict: bool) -> None:
    """Validate the specification."""
    project_path = Path(path).resolve()
    method_path = project_path / "methodology.yaml"

    if not method_path.exists():
        console.print(
            "[red]Error:[/red] methodology.yaml not found. Run 'spec-editor init'."
        )
        raise SystemExit(1)

    console.print(f"[bold]Validating specification[/bold] at {project_path}")
    console.print()

    # Step 1: Load methodology
    method = load_methodology(method_path)
    aspect_names = ", ".join(a.name for a in method.aspects)
    console.print(f"  [dim]Methodology:[/dim] {method.name} v{method.version}")
    console.print(f"  [dim]Aspects:[/dim] {aspect_names}")

    # Step 2: Read elements
    storage = FilesystemStorage(project_path)
    all_summaries = storage.list_all()
    elem_count = len(all_summaries)
    console.print(f"  [dim]Elements found:[/dim] {elem_count}")
    console.print()

    # Step 3: Run validation
    report: ValidationReport = validate(storage, method)

    # Categorise errors by what they check
    def _cat(field: str | None) -> str:
        if field is None:
            return "read"
        if field == "id":
            return "duplicates"
        if field in ("aspect", "element_type"):
            return "methodology types"
        if field == "title":
            return "required fields"
        if field in ("parent", "children"):
            return "parent/children refs"
        if field and field.startswith("relationships"):
            return "relationship types"
        return "other"

    err_by_cat: dict[str, list] = defaultdict(list)
    warn_by_cat: dict[str, list] = defaultdict(list)
    for e in report.errors:
        err_by_cat[_cat(e.field)].append(e)
    for w in report.warnings:
        warn_by_cat[_cat(w.field)].append(w)

    # Step 4: Show checklist
    checks = [
        ("read", "Elements readable"),
        ("duplicates", "No duplicate IDs"),
        ("required fields", "Required fields (aspect, type, title)"),
        ("parent/children refs", "Parent/children references"),
        ("relationship types", "Relationship types vs methodology"),
        ("methodology types", "Aspect & element types vs methodology"),
    ]

    for cat, label in checks:
        errors = err_by_cat.get(cat, [])
        warnings = warn_by_cat.get(cat, [])
        if not errors and not warnings:
            console.print(f"  [green]OK[/green]    {label}")
        elif errors:
            console.print(f"  [red]FAIL[/red]  {label} ({len(errors)} errors)")
        else:
            console.print(
                f"  [yellow]WARN[/yellow]  {label} ({len(warnings)} warnings)"
            )

    console.print()

    # Step 5: Show details for failures
    if report.errors:
        console.print("[red]Errors:[/red]")
        for err in report.errors:
            loc = f"{err.element_id}:{err.field}" if err.element_id else "-"
            console.print(f"  [red]X[/red] [{loc}] {err.message}")

    if report.warnings:
        console.print()
        console.print("[yellow]Warnings:[/yellow]")
        for warn in report.warnings:
            loc = f"{warn.element_id}:{warn.field}" if warn.element_id else "-"
            console.print(f"  [yellow]![/yellow] [{loc}] {warn.message}")

    console.print()
    if report.passed and not report.warnings:
        console.print(f"[green]Passed. {elem_count} elements, no errors.[/green]")
    elif report.passed:
        console.print(
            f"[yellow]Passed with {len(report.warnings)} warning(s).[/yellow]"
        )
    else:
        console.print(
            f"[red]Failed: {len(report.errors)} error(s), "
            f"{len(report.warnings)} warning(s).[/red]"
        )


# ======================================================================
# status
# ======================================================================


@cli.command()
@click.option(
    "--path",
    "-p",
    default=".",
    envvar="SPEC_EDITOR_PROJECT",
    type=click.Path(exists=True),
    help="Path to project (or SPEC_EDITOR_PROJECT)",
)
@click.option("--aspect", "-a", default=None, help="Show only the specified aspect")
@click.option("--metrics", "-m", is_flag=True, help="Show metrics")
def status_cmd(path: str, aspect: str | None, metrics: bool) -> None:
    """Show the state of the specification."""
    project_path = Path(path).resolve()

    # Pre-flight environment check (spec-editor binary is critical)
    check_environment(project_path, require_redis=False)
    storage = FilesystemStorage(project_path)

    if metrics:
        _show_metrics(storage)

    if aspect:
        elements = storage.list_aspect(aspect)
    else:
        elements = storage.list_all()

    table = Table(title=f"Specification elements ({len(elements)})")
    table.add_column("ID", style="cyan")
    table.add_column("Title", style="white")
    table.add_column("Aspect", style="green")
    table.add_column("Type", style="blue")
    table.add_column("Status", style="yellow")
    table.add_column("Parent", style="dim")

    for el in sorted(elements, key=lambda e: (e.aspect, e.id)):
        table.add_row(
            el.id,
            el.title,
            el.aspect,
            el.element_type,
            el.status.value,
            el.parent or "-",
        )

    console.print(table)


def _show_metrics(storage: FilesystemStorage) -> None:
    report: MetricsReport = compute_metrics(storage)

    table = Table(title="Connectivity metrics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Total elements", str(report.total_elements))
    table.add_row("Total relationships", str(report.total_relationships))
    table.add_row("Cross-aspect relationships", str(report.cross_aspect_relationships))
    table.add_row("Connectivity index", f"{report.connectivity_index:.4f}")
    table.add_row("Orphan elements", str(report.orphan_elements))
    table.add_row("Coverage (confirmed)", f"{report.coverage_ratio:.2%}")
    console.print(table)

    if report.aspects:
        aspect_table = Table(title="By aspect")
        aspect_table.add_column("Aspect")
        aspect_table.add_column("Elements")
        for name, count in sorted(report.aspects.items()):
            aspect_table.add_row(name, str(count))
        console.print(aspect_table)


# ======================================================================
# log
# ======================================================================


@cli.command()
@click.argument("agent", required=False, default=None)
@click.option(
    "--path",
    "-p",
    default=".",
    envvar="SPEC_EDITOR_PROJECT",
    type=click.Path(exists=True),
    help="Path to project (or SPEC_EDITOR_PROJECT)",
)
@click.option(
    "--follow",
    "-f",
    is_flag=True,
    help="Follow log in real time (like tail -f)",
)
@click.option(
    "--lines",
    "-n",
    type=int,
    default=0,
    help="Show last N lines (0 = all)",
)
def log_cmd(path: str, follow: bool, lines: int, agent: str | None) -> None:
    """Show agent logs — combined or per-agent.

    AGENT: optional agent name (coding, tester, project-manager, etc.)
    Without AGENT, tails ALL logs/agent-*.log files together.
    With AGENT, shows only logs/agent-<AGENT>.log.

    Examples:
      spec-editor log -f            # follow ALL agent logs combined
      spec-editor log -n50          # last 50 lines from all agents
      spec-editor log coding -f     # follow coding agent log only
      spec-editor log tester -n20   # last 20 lines of tester log
    """
    import time

    project_path = Path(path).resolve()
    logs_dir = project_path / "logs"

    if agent:
        _print_agent_log(project_path, agent, follow, lines)
        return

    # ── Combined log: all logs/MOD-*-agent/structured.jsonl ──
    agent_logs = sorted(logs_dir.glob("MOD-*-agent/structured.jsonl")) if logs_dir.is_dir() else []

    if not agent_logs:
        console.print("[yellow]No agent logs found in logs/[/yellow]")
        console.print("Run 'spec-editor cycle --watch' to start agents.")
        return

    # Print existing content (last N lines per file)
    if lines > 0 or not follow:
        _print_combined_logs(agent_logs, lines)

    if follow:
        console.print(
            f"[dim]Following {len(agent_logs)} agent log(s) (Ctrl+C to exit)...[/dim]"
        )
        _follow_combined_logs(agent_logs)


def _print_agent_log(
    project_path: Path, agent: str, follow: bool, lines: int
) -> None:
    """Print (and optionally follow) an agent log file."""
    log_file = project_path / "logs" / f"MOD-{agent}-agent" / "structured.jsonl"

    if not log_file.exists():
        console.print(f"[yellow]Agent log not found: {log_file}[/yellow]")
        console.print("Available agents: coding, tester, project-manager, analyst-manager, devops")
        return

    if lines > 0:
        # Show last N lines
        with open(log_file, encoding="utf-8") as f:
            all_lines = f.readlines()
            for line in all_lines[-lines:]:
                console.print(line.rstrip())
    else:
        # Show all
        with open(log_file, encoding="utf-8") as f:
            for line in f:
                console.print(line.rstrip())

    if follow:
        console.print(f"[dim]Following {log_file.name} (Ctrl+C to exit)...[/dim]")
        seen = log_file.stat().st_size
        try:
            while True:
                time.sleep(1)
                current_size = log_file.stat().st_size
                if current_size > seen:
                    with open(log_file, encoding="utf-8") as f:
                        f.seek(seen)
                        for line in f:
                            console.print(line.rstrip())
                    seen = current_size
                elif not log_file.exists():
                    break
        except KeyboardInterrupt:
            console.print("[dim]Stopped.[/dim]")


# ── Combined log helpers ──

_AGENT_COLORS: dict[str, str] = {
    "coding": "blue",
    "tester": "yellow",
    "project-manager": "magenta",
    "analyst-manager": "green",
    "devops": "cyan",
}


def _agent_name_from_path(log_path: Path) -> str:
    """Extract agent name from log filename: agent-coding.log → coding."""
    name = log_path.stem  # agent-coding
    if name.startswith("agent-"):
        return name[6:]  # coding
    return name


def _print_combined_logs(log_files: list[Path], lines: int = 0) -> None:
    """Print last N lines from each agent log, prefixed with colored [agent]."""
    import time as _time

    for log_file in log_files:
        agent = _agent_name_from_path(log_file)
        color = _AGENT_COLORS.get(agent, "white")

        with open(log_file, encoding="utf-8") as f:
            all_lines = f.readlines()
            if lines > 0:
                all_lines = all_lines[-lines:]
            if all_lines:
                console.print(f"[bold {color}]── {agent} ({log_file.name})[/bold {color}]")
                for line in all_lines:
                    console.print(f"  [{color}][{agent}][/{color}] {line.rstrip()}")


def _follow_combined_logs(log_files: list[Path]) -> None:
    """Tail multiple agent log files, interleaving lines with colored prefixes."""
    import time as _time

    # Track file sizes
    sizes: dict[str, int] = {}
    for lf in log_files:
        try:
            sizes[str(lf)] = lf.stat().st_size
        except OSError:
            sizes[str(lf)] = 0

    try:
        while True:
            _time.sleep(1)
            for log_file in log_files:
                try:
                    current_size = log_file.stat().st_size
                except OSError:
                    continue
                prev_size = sizes.get(str(log_file), 0)
                if current_size > prev_size:
                    agent = _agent_name_from_path(log_file)
                    color = _AGENT_COLORS.get(agent, "white")
                    with open(log_file, encoding="utf-8") as f:
                        f.seek(prev_size)
                        for line in f:
                            line = line.rstrip()
                            if line:
                                console.print(
                                    f"[{color}][{agent}][/{color}] {line}"
                                )
                    sizes[str(log_file)] = current_size
    except KeyboardInterrupt:
        console.print("[dim]Stopped.[/dim]")


def _print_log(log_file: Path, lines: int = 0) -> None:
    """Print all (or last N) lines from a dialogue log file."""
    import json

    with open(log_file, encoding="utf-8") as f:
        if lines > 0:
            all_lines = f.readlines()
            for line in all_lines[-lines:]:
                try:
                    entry = json.loads(line)
                    _print_entry(entry)
                except json.JSONDecodeError:
                    console.print(line.rstrip())
        else:
            for line in f:
                try:
                    entry = json.loads(line)
                    _print_entry(entry)
                except json.JSONDecodeError:
                    console.print(line.rstrip())


def _print_entry(entry):
    """Print a single log entry."""
    ts = entry.get("timestamp", "")[:19]
    agent = entry.get("agent", "?")
    # Handle trace entries (detailed tool-call logging)
    if "trace" in entry:
        console.print(f"[dim]{ts}[/dim] {entry['trace']}")
        return

    if "decision" in entry:
        reason = entry.get("reason", "")
        decision = entry.get("decision", "")
        if decision == "TERMINATED":
            # Pretty completion summary
            console.print(f"\n[bold green]═══   ═══[/bold green]")
            for line in reason.split("\n"):
                console.print(f"  {line}")
            console.print()
        elif decision == "health_check":
            console.print(f"[dim]{ts}[/dim] [{agent}] {reason[:120]}")
        else:
            console.print(
                f"[yellow]{ts}[/yellow] [bold]{entry['agent']}: {entry['decision']}[/bold]"
                f" — {reason[:200]}"
            )
    else:
        content = entry.get("content", "") or ""
        tools = entry.get("tool_calls", [])
        # Format tool_calls compactly
        tool_parts = []
        for tc in tools:
            if isinstance(tc, dict):
                name = tc.get("name", "?")
                args = tc.get("args", {})
            else:
                name = str(tc)
                args = {}
            if args:
                # Show arguments inline: write_element(id=MOD-042, title=...)
                arg_str = ", ".join(f"{k}={v}" for k, v in args.items())
                tool_parts.append(f"{name}({arg_str})")
            else:
                tool_parts.append(name)

        # Main output: timestamp + agent + content
        content_short = content[:200].replace("\n", " ")
        console.print(f"[dim]{ts}[/dim] [{agent}] {content_short}")

        if tool_parts:
            # Tools on the next line with indentation
            tools_str = " | ".join(tool_parts[:8])  # max 8 tools
            if len(tool_parts) > 8:
                tools_str += f" | +{len(tool_parts) - 8} more"
            console.print(f"     [dim]↳ {tools_str}[/dim]")

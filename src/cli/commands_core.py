"""CLI subcommand."""

import json
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

# validate
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
def log_cmd(path: str, follow: bool) -> None:
    """Show the dialogue log. With -f, follow updates in real time."""
    project_path = Path(path).resolve()
    log_file = project_path / "dialogue.jsonl"

    if not log_file.exists():
        console.print("[yellow]Dialogue log not found.[/yellow]")
        console.print("Run 'spec-editor run' to create a log.")
        return

    import json
    import time

    _print_log(log_file)
    if follow:
        console.print("[dim]Waiting for new entries (Ctrl+C to exit)...[/dim]")
        seen = log_file.stat().st_size
        last_progress = time.time()
        aspects_dir = project_path / "aspects"
        try:
            while True:
                time.sleep(1)
                current_size = log_file.stat().st_size
                if current_size > seen:
                    with open(log_file, encoding="utf-8") as f:
                        f.seek(seen)
                        for line in f:
                            try:
                                entry = json.loads(line)
                                _print_entry(entry)
                            except json.JSONDecodeError:
                                console.print(line.rstrip())
                    seen = current_size
                    last_progress = time.time()
                elif time.time() - last_progress > 5:
                    # Show progress: element count in aspects/
                    count = (
                        len(list(aspects_dir.rglob("*.md")))
                        if aspects_dir.is_dir()
                        else 0
                    )
                    console.print(
                        f"[dim]  ... elements: {count}, waiting for agent messages ...[/dim]",
                        end="\r",
                    )
                    last_progress = time.time()
                elif not log_file.exists():
                    break
        except KeyboardInterrupt:
            console.print("[dim]Stopped.[/dim]")


def _print_log(log_file):
    """Print all lines from the log file."""
    import json

    with open(log_file, encoding="utf-8") as f:
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

"""CLI commands for spec-editor."""

import shutil
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.table import Table

from src.config import get_logger
from src.config.methodology import load_methodology
from src.config.settings import (
    AgentConfig,
    AgentsConfig,
    create_provider,
)
from src.mcp.metrics import MetricsReport, compute_metrics
from src.mcp.validator import ValidationReport, validate
from src.storage.filesystem import FilesystemStorage
from src.tracing import implements

logger = get_logger(__name__)
console = Console()

_BUILTIN_METHODOLOGIES = Path(__file__).parent.parent.parent / "methodologies"

_README_TEMPLATE = """\
# Project Description

Describe the target system for which requirements are being developed.

## Purpose

## Key Features

## Users and Roles

## Constraints
"""


@click.group()
@implements("SRC-008")
@implements("MOD-005")
def cli() -> None:
    """Spec Editor — AI agents for requirements development."""
    pass


# ======================================================================
# init
# ======================================================================


@cli.command()
@click.argument("path", type=click.Path())
@click.option("--methodology", "-m", default="waterfall", help="Methodology name")
@click.option(
    "--agents",
    "-a",
    default=None,
    type=click.Path(exists=True),
    help="YAML with agent configuration",
)
def init(path: str, methodology: str, agents: str | None) -> None:
    """Initialize a new specification project at PATH."""
    project_path = Path(path).resolve()

    if project_path.exists() and any(project_path.iterdir()):
        console.print(f"[red]Error:[/red] directory '{project_path}' is not empty.")
        raise SystemExit(1)

    method_path = _BUILTIN_METHODOLOGIES / f"{methodology}.yaml"
    if not method_path.exists():
        available = [p.stem for p in _BUILTIN_METHODOLOGIES.glob("*.yaml")]
        console.print(
            f"[red]Error:[/red] methodology '{methodology}' not found. "
            f"Available: {', '.join(available)}"
        )
        raise SystemExit(1)

    try:
        method = load_methodology(method_path)
    except Exception as exc:
        console.print(f"[red]Error loading methodology:[/red] {exc}")
        raise SystemExit(1)

    project_path.mkdir(parents=True, exist_ok=True)
    aspects_dir = project_path / "aspects"
    aspects_dir.mkdir(exist_ok=True)

    source_dir = project_path / "source"
    source_dir.mkdir(exist_ok=True)
    readme_path = source_dir / "readme.md"
    readme_path.write_text(_README_TEMPLATE, encoding="utf-8")

    shutil.copy(method_path, project_path / "methodology.yaml")

    # Copy skills.yaml if present
    skills_path = _BUILTIN_METHODOLOGIES.parent / "skills.yaml"
    if skills_path.exists():
        shutil.copy(skills_path, project_path / "skills.yaml")

    agents_config = _create_default_agents_config()
    if agents:
        try:
            agents_config = AgentsConfig.from_yaml(Path(agents))
        except Exception as exc:
            console.print(f"[yellow]Warning:[/yellow] failed to load {agents}: {exc}")
            console.print("Using default configuration.")

    agents_yaml = {
        "agents": {
            "agent_1": agents_config.agent_1.model_dump(),
            "agent_2": agents_config.agent_2.model_dump(),
            "orchestrator": agents_config.orchestrator.model_dump(),
        },
        "max_rounds": agents_config.max_rounds,
        "max_time_minutes": agents_config.max_time_minutes,
    }
    with open(project_path / "agents.yaml", "w", encoding="utf-8") as f:
        yaml.dump(
            agents_yaml,
            f,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )

    console.print(f"[green]Project created:[/green] {project_path}")
    console.print(f"  Methodology: {method.name} v{method.version}")
    console.print(f"  Aspects: {len(method.aspects)}")
    console.print(
        f"  Agents: {agents_config.agent_1.model} / {agents_config.agent_2.model}"
    )
    console.print(f"  Orchestrator: {agents_config.orchestrator.model}")
    console.print(f"\n  Next: cd {project_path} && spec-editor run")


def _create_default_agents_config() -> AgentsConfig:
    return AgentsConfig(
        agent_1=AgentConfig(
            provider="deepseek", model="deepseek/deepseek-chat", temperature=0.7
        ),
        agent_2=AgentConfig(
            provider="deepseek", model="deepseek/deepseek-chat", temperature=0.7
        ),
        orchestrator=AgentConfig(
            provider="deepseek", model="deepseek/deepseek-chat", temperature=0.3
        ),
        max_rounds=20,
        max_time_minutes=30,
    )


# ======================================================================
# validate
# ======================================================================


@cli.command()
@click.option(
    "--path",
    "-p",
    required=True,
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

    method = load_methodology(method_path)
    storage = FilesystemStorage(project_path)
    report: ValidationReport = validate(storage, method)

    if report.errors:
        console.print("[red]Validation errors:[/red]")
        for err in report.errors:
            loc = f"{err.element_id}:{err.field}" if err.element_id else "-"
            console.print(f"  [red]✗[/red] [{loc}] {err.message}")

    if report.warnings:
        console.print("[yellow]Warnings:[/yellow]")
        for warn in report.warnings:
            loc = f"{warn.element_id}:{warn.field}" if warn.element_id else "-"
            console.print(f"  [yellow]⚠[/yellow] [{loc}] {warn.message}")

    if report.passed and not report.warnings:
        console.print("[green]✓ Validation passed. No errors.[/green]")
    elif report.passed:
        console.print("[yellow]✓ Validation passed with warnings.[/yellow]")


# ======================================================================
# status
# ======================================================================


@cli.command()
@click.option(
    "--path",
    "-p",
    required=True,
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
    required=True,
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


@cli.command(name="questions")
@click.option(
    "--path",
    "-p",
    default=None,
    envvar="SPEC_EDITOR_PROJECT",
    type=click.Path(exists=True),
    help="Project path",
)
@click.option("--list", "list_flag", is_flag=True, help="Show open questions")
@click.option("--answer", "-a", nargs=2, default=None, help="Answer: Q-0001 'text'")
@click.option("--dismiss", "-d", default=None, help="Dismiss question by ID")
def questions(
    path: str, list_flag: bool, answer: tuple | None, dismiss: str | None
) -> None:
    """Manage asynchronous agent questions (questions.jsonl).

    \b
    View:    spec-editor questions -p . --list
    Answer:  spec-editor questions -p . --answer Q-0001 'ISO 8601'
    Dismiss: spec-editor questions -p . --dismiss Q-0001
    """
    from pathlib import Path

    from src.agents.questions import QuestionList

    project_path = Path(path).resolve()
    ql = QuestionList(project_path)

    if answer:
        qid, text = answer
        q = ql.answer(qid, text)
        if q:
            console.print(f"[green]OK[/green] Question {qid} answered: {text}")
        else:
            console.print(
                f"[red]ERR[/red] Question {qid} not found or already answered"
            )
    elif dismiss:
        q = ql.answer(dismiss, "[dismissed]")
        if q:
            console.print(f"[yellow]DISMISS[/yellow] Question {dismiss} dismissed")
        else:
            console.print(f"[red]ERR[/red] Question {dismiss} not found")
    else:
        questions = ql.list_open()
        if not questions:
            console.print("[dim]No open questions[/dim]")
        else:
            table = Table(title=f"Open questions ({len(questions)})")
            table.add_column("ID", style="cyan")
            table.add_column("Agent", style="green")
            table.add_column("Question", style="white")
            table.add_column("Options", style="dim")
            for q in questions:
                table.add_row(
                    q.id,
                    q.agent,
                    q.question,
                    ", ".join(q.options) if q.options else "-",
                )
            console.print(table)


@cli.command(name="deprecate")
@click.option(
    "--path",
    "-p",
    default=None,
    envvar="SPEC_EDITOR_PROJECT",
    type=click.Path(exists=True),
    help="Project path",
)
@click.option(
    "--from-file",
    "-f",
    default=None,
    type=click.Path(exists=True),
    help="File describing what to remove",
)
@click.option("--text", "-t", default=None, help="Text describing what to remove")
@click.option("--dry-run", is_flag=True, help="Show without changes")
def deprecate_cmd(
    path: str, from_file: str | None, text: str | None, dry_run: bool
) -> None:
    """Deprecate requirements by file or text.

    \b
    By file: spec-editor deprecate -p . -f remove.txt
    Dry-run: spec-editor deprecate -p . -f remove.txt --dry-run
    """
    import asyncio
    from pathlib import Path

    from src.config.settings import AgentConfig, create_provider
    from src.ingestion.manager import deprecate_from_file
    from src.storage.filesystem import FilesystemStorage

    if not from_file and not text:
        console.print("[red]Specify --from-file or --text[/red]")
        return

    project_path = Path(path).resolve()
    storage = FilesystemStorage(project_path)

    if text:
        import tempfile

        tmp = Path(tempfile.mktemp(suffix=".txt"))
        tmp.write_text(text, encoding="utf-8")
        from_file = str(tmp)

    provider = create_provider(
        AgentConfig(provider="deepseek", model="deepseek/deepseek-chat")
    )

    async def _run():
        return await deprecate_from_file(
            storage, provider, Path(from_file), dry_run=dry_run
        )

    result = asyncio.run(_run())

    if "error" in result:
        console.print(f"[red]{result['error']}[/red]")
        return

    if not result["deprecated"]:
        console.print("[yellow]No matches found[/yellow]")
        return

    action = "Will be deprecated" if dry_run else "Deprecated"
    table = Table(title=f"{action} ({len(result['deprecated'])})")
    table.add_column("ID", style="cyan")
    table.add_column("Title", style="white")
    table.add_column("Status", style="yellow")
    for item in result["deprecated"]:
        table.add_row(item["id"], item["title"], item["status"])
    console.print(table)

    if result.get("not_found"):
        console.print(f"[dim]Not found: {', '.join(result['not_found'])}[/dim]")


@cli.command(name="restore")
@click.option(
    "--path",
    "-p",
    default=None,
    envvar="SPEC_EDITOR_PROJECT",
    type=click.Path(exists=True),
    help="Project path",
)
@click.argument("ids", nargs=-1)
def restore_cmd(path: str, ids: tuple[str, ...]) -> None:
    """Restore deprecated requirements.

    spec-editor restore -p . NFR-export-pdf MOD-notifications
    """
    from pathlib import Path

    from src.ingestion.manager import restore_elements
    from src.storage.filesystem import FilesystemStorage

    if not ids:
        console.print("[red]Specify requirement IDs[/red]")
        return

    project_path = Path(path).resolve()
    storage = FilesystemStorage(project_path)
    result = restore_elements(storage, list(ids))

    if result["restored"]:
        console.print(f"[green]Restored: {len(result['restored'])}[/green]")
        for item in result["restored"]:
            console.print(f"  {item['id']}: {item['title']}")
    if result.get("not_deprecated"):
        for item in result["not_deprecated"]:
            console.print(f"[dim]{item['id']}: was not deprecated[/dim]")
    if result.get("not_found"):
        console.print(f"[red]Not found: {', '.join(result['not_found'])}[/red]")


@cli.command()
@click.option(
    "--path",
    "-p",
    default=None,
    envvar="SPEC_EDITOR_PROJECT",
    type=click.Path(exists=True),
    help="Project path",
)
@click.option(
    "--output",
    "-o",
    default=None,
    type=click.Path(),
    help="Output file (default: stdout)",
)
@click.option(
    "--template",
    default="srs_template.yaml",
    type=click.Path(exists=True),
    help="SRS template (for srs format only)",
)
@click.option(
    "--format",
    "-f",
    "format",
    default="srs",
    type=click.Choice(["srs", "trlc", "openapi", "jira", "compliance"]),
    help="Export format",
)
def export(
    path: str,
    output: str | None,
    template: str,
    format: str = "srs",
) -> None:
    """Export the specification to various formats.

    Supported formats:
      srs      — IEEE 830 SRS document (Markdown)
      trlc     — TRLC requirements-as-code (BMW-compatible)
      openapi  — OpenAPI 3.0 YAML (api-first methodology)
      jira     — Jira CSV for sprint backlog import (agile methodology)
      compliance — Compliance traceability matrix XLSX (regulatory methodology)
    """
    from pathlib import Path

    from src.storage.filesystem import FilesystemStorage

    project_path = Path(path).resolve()
    storage = FilesystemStorage(project_path)

    if format == "trlc":
        _export_trlc(storage, project_path, output)
    elif format == "openapi":
        _export_openapi(storage, project_path, output)
    elif format == "jira":
        _export_jira(storage, project_path, output)
    elif format == "compliance":
        _export_compliance(storage, project_path, output)
    else:
        _export_srs(storage, project_path, output, template)


@cli.command(name="codegen")
@click.option(
    "--path",
    "-p",
    default=None,
    envvar="SPEC_EDITOR_PROJECT",
    type=click.Path(exists=True),
    help="Path to project",
)
@click.option(
    "--output",
    "-o",
    default="./generated",
    help="Output directory for generated code",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be generated without writing files",
)
def codegen_cmd(path: str, output: str, dry_run: bool) -> None:
    """Generate code skeletons from specification elements.

    Uses Jinja2 templates configured in codegen.yaml.
    Each element is rendered through its mapped template
    and written to the output directory.

    spec-editor codegen -p . -o ./src
    spec-editor codegen -p . --dry-run
    """
    from pathlib import Path

    from src.codegen.engine import CodeGenerator
    from src.storage.filesystem import FilesystemStorage

    project_path = Path(path).resolve()
    storage = FilesystemStorage(project_path)
    elements = storage.list_all()

    if not elements:
        console.print("[yellow]No elements found in the specification.[/yellow]")
        return

    gen = CodeGenerator()
    output_dir = Path(output).resolve()

    if dry_run:
        console.print(f"[dim]Dry run — would write to {output_dir}[/dim]\n")
    else:
        console.print(f"[dim]Writing to {output_dir}[/dim]\n")

    results = []
    for summary in elements:
        try:
            el = storage.read_element(summary.id)
            result = gen.generate_element(el, output_dir, dry_run=dry_run)
            results.append(result)
        except Exception as exc:
            results.append(
                {
                    "element_id": summary.id,
                    "status": "error",
                    "reason": str(exc),
                }
            )

    created = [r for r in results if r["status"] == "created"]
    dry = [r for r in results if r["status"] == "dry_run"]
    skipped = [r for r in results if r["status"] == "skipped"]
    errors = [r for r in results if r["status"] == "error"]

    if dry_run:
        table = Table(title=f"Code Generation Preview ({len(dry)} files)")
        table.add_column("Element", style="cyan")
        table.add_column("Template", style="dim")
        table.add_column("Output File")
        for r in dry:
            table.add_row(r["element_id"], r.get("template", ""), r["file"])
        console.print(table)
    else:
        console.print(f"[green]Generated: {len(created)} files[/green]")

    if skipped:
        console.print(
            f"[dim]Skipped: {len(skipped)} (no template for element type)[/dim]"
        )
    if errors:
        console.print(f"[red]Errors: {len(errors)}[/red]")
        for e in errors:
            console.print(
                f"  [red]{e['element_id']}: {e.get('reason', 'unknown')}[/red]"
            )


@cli.command(name="hooks")
@click.option(
    "--config",
    "-c",
    default="hooks.yaml",
    type=click.Path(exists=True),
    help="Path to hooks.yaml",
)
@click.option(
    "--fetch-since",
    default=None,
    help="Load history from date (YYYY-MM-DD). No flag — live mode",
)
@click.option(
    "--fetch-limit",
    default=200,
    type=int,
    help="Max messages when loading history (default: 200)",
)
def hooks_start(config: str, fetch_since: str | None, fetch_limit: int) -> None:
    """Start a Telegram hook for receiving requirements.

    \b
    Live mode:        spec-editor hooks
    Fetch history:    spec-editor hooks --fetch-since 2026-01-01
    History + limit:  spec-editor hooks --fetch-since 2026-01-01 --fetch-limit 500
    """
    import asyncio
    from datetime import datetime
    from pathlib import Path

    from src.ingestion.telegram_hook import HookConfig, TelegramWatcher

    try:
        hook_config = HookConfig.from_file(Path(config))
    except Exception as e:
        console.print(f"[red]Error loading {config}: {e}[/red]")
        return

    if not hook_config.api_id or not hook_config.api_hash:
        console.print(
            "[red]hooks.yaml: specify api_id and api_hash\n"
            "Get at https://my.telegram.org/apps[/red]"
        )
        return

    if hook_config.api_id > 2_147_483_647:
        console.print(
            "[red]api_id   (max 2147483647).\n https://my.telegram.org/apps[/red]"
        )
        return

    watcher = TelegramWatcher(hook_config)
    console.print("[bold] Telegram-[/bold]")
    total_chats = sum(len(p.chats) for p in hook_config.projects)
    console.print(f"  : {len(hook_config.projects)}, : {total_chats}")

    if fetch_since:
        since = datetime.fromisoformat(fetch_since)
        console.print(f"  :   from {fetch_since} (limit: {fetch_limit})")
        asyncio.run(watcher.fetch_history(since=since, limit=fetch_limit))
    else:
        console.print(f"  : live-")
        asyncio.run(watcher.start())


@cli.command(name="analyze")
@click.option(
    "--path",
    "-p",
    default=None,
    envvar="SPEC_EDITOR_PROJECT",
    type=click.Path(exists=True),
    help="Project path",
)
@click.option(
    "--file",
    "-f",
    required=True,
    type=click.Path(exists=True),
    help="Source directory name",
)
@click.option(
    "--auto-apply",
    is_flag=True,
    help="Generate SRC and deprecate",
)
def analyze_cmd(path: str, file: str, auto_apply: bool) -> None:
    """Analyze a requirements file: new, duplicates, replacements.

    spec-editor analyze -p . -f new_features.txt
    spec-editor analyze -p . -f new_features.txt --auto-apply
    """
    import asyncio
    from pathlib import Path

    from src.config.settings import AgentConfig, create_provider
    from src.ingestion.analyzer import ConflictDetector, DiffEngine
    from src.ingestion.preprocessor import FactExtractor, SourcePreprocessor
    from src.storage.filesystem import FilesystemStorage

    project_path = Path(path).resolve()
    storage = FilesystemStorage(project_path)
    provider = create_provider(
        AgentConfig(provider="deepseek", model="deepseek/deepseek-chat")
    )
    file_path = Path(file)

    # Read the file
    text = SourcePreprocessor.read_file(file_path)
    extractor = FactExtractor(provider)
    fact = extractor.extract(text)
    diff_engine = DiffEngine(storage)

    console.print(f"\n[bold]═══ : {file_path.name} ═══[/bold]\n")
    console.print(f"[dim]: {fact.title}[/dim]\n")

    diff = diff_engine.analyze(fact.title, fact.description)

    if not diff.is_duplicate:
        console.print("[bold green]🆕  [/bold green]")
        console.print(f"  {fact.title}")
        console.print(f"  {fact.description[:200]}")

        if auto_apply:
            from src.storage.models import Element, ElementStatus, Provenance

            next_id = 1
            for s in storage.list_all():
                if s.id.startswith("SRC-"):
                    try:
                        n = int(s.id.split("-")[1])
                        if n >= next_id:
                            next_id = n + 1
                    except:
                        pass
            src_id = f"SRC-{next_id:03d}"
            el = Element(
                aspect="sources",
                element_type="source",
                id=src_id,
                title=fact.title,
                content=fact.description,
                status=ElementStatus.DRAFT,
                provenance=Provenance(source=file_path.name),
            )
            storage.write_element(el)
            console.print(f"[green]  ✓  {src_id}[/green]")
        else:
            console.print(f"[dim]  Run with --auto-apply to generate SRC[/dim]")

    elif diff.conflicts:
        console.print("[bold yellow]🔄 agent limit reached[/bold yellow]")
        console.print(f"  : {fact.title}")
        console.print(f"  : {diff.matched_id} — {diff.matched_title}")
        for c in diff.conflicts:
            console.print(f"  [yellow]⚠ {c}[/yellow]")

        if auto_apply:
            storage.write_element(
                storage.read_element(diff.matched_id).model_copy(
                    update={"status": ElementStatus("deprecated")}
                )
            )
            console.print(f"[yellow]  ✓ {diff.matched_id} → deprecated[/yellow]")

            from src.storage.models import Element, Provenance

            next_id = 1
            for s in storage.list_all():
                if s.id.startswith("SRC-"):
                    try:
                        n = int(s.id.split("-")[1])
                        if n >= next_id:
                            next_id = n + 1
                    except:
                        pass
            src_id = f"SRC-{next_id:03d}"
            el = Element(
                aspect="sources",
                element_type="source",
                id=src_id,
                title=fact.title,
                content=fact.description,
                status=ElementStatus.DRAFT,
                provenance=Provenance(source=file_path.name),
            )
            storage.write_element(el)
            console.print(f"[green]  ✓  {src_id}[/green]")
        else:
            console.print(
                f"[dim]  Run with --auto-apply to deprecate + generate SRC[/dim]"
            )

    else:
        console.print("[bold cyan]📋 [/bold cyan]")
        console.print(f"  {fact.title}")
        console.print(f"  → {diff.matched_id}: {diff.matched_title}")


@cli.command()
@click.option(
    "--path",
    "-p",
    default=None,
    envvar="SPEC_EDITOR_PROJECT",
    type=click.Path(exists=True),
    help="Path to code directory",
)
@implements("SRC-007")
@implements("MOD-003")
def mcp(path: str | None) -> None:
    """Start MCP server for external agents (stdio/json-rpc).
    -p is optional. Without -p: project is switched via the switch_project tool.

    \b
    Connecting to ZED — add to ~/.config/zed/settings.json:
    {
      "mcp_servers": {
        "spec-editor": {
          "command": "/path/to/.venv/bin/spec-editor",
          "args": ["mcp", "-p", "/path/to/project"]
        }
      }
    }
    "
    """
    from src.mcp.server import mcp_server as _server

    _server.callback(path if path else None)


# ---------------------------------------------------------------------------
# Export helper functions
# ---------------------------------------------------------------------------


def _export_srs(storage, project_path, output, template):
    """Export to IEEE 830 SRS document."""
    from pathlib import Path

    from src.export.pipeline import pipeline_from_config

    template_path = (
        Path(template) if Path(template).is_absolute() else Path(template)
    )

    cfg = {
        "gatherer": "srs",
        "formatter": "markdown",
        "transport": "file",
    }
    pipeline = pipeline_from_config(cfg, storage, project_path)
    out_path, data = pipeline.run(
        storage,
        template_path,
        project_path,
        transport_config={"output": output or str(project_path / "srs.md")},
    )

    console.print(f"[green]SRS saved to {out_path}[/green]")
    console.print(f"  Sections: {len(data.sections)}")
    total = sum(len(s.elements) for s in data.sections)
    console.print(f"  Elements: {total}")
    if data.metadata.get("duplicates"):
        console.print(
            f"  [yellow]Duplicates: {data.metadata['duplicates']}[/yellow]"
        )


def _export_trlc(storage, project_path, output):
    """Export to TRLC format."""
    from pathlib import Path

    from src.export.trlc import TRLCExporter

    elements = []
    for summary in storage.list_all():
        try:
            elements.append(storage.read_element(summary.id))
        except Exception:
            pass

    exporter = TRLCExporter()
    out_path = Path(output) if output else project_path / "requirements.trlc"
    exporter.export(elements, out_path)
    console.print(f"[green]TRLC saved to {out_path}[/green]")
    console.print(f"  Elements: {len(elements)}")


def _export_openapi(storage, project_path, output):
    """Export to OpenAPI 3.0 YAML."""
    from pathlib import Path

    from src.export.openapi_exporter import OpenAPIExporter

    service = None
    endpoints = []
    schemas = []
    auth_schemes = []

    for summary in storage.list_all():
        try:
            el = storage.read_element(summary.id)
        except Exception:
            continue
        if el.element_type == "service":
            service = el
        elif el.element_type == "endpoint":
            endpoints.append(el)
        elif el.element_type == "schema":
            schemas.append(el)
        elif el.element_type == "auth_scheme":
            auth_schemes.append(el)

    if service is None:
        console.print(
            "[red]Error:[/red] no service element found. "
            "Create a service element in the 'api' aspect first."
        )
        return

    exporter = OpenAPIExporter()
    out_path = Path(output) if output else project_path / "openapi.yaml"
    exporter.export(service, endpoints, schemas, auth_schemes, out_path)
    console.print(f"[green]OpenAPI spec saved to {out_path}[/green]")
    console.print(
        f"  Endpoints: {len(endpoints)}, "
        f"Schemas: {len(schemas)}, "
        f"Auth: {len(auth_schemes)}"
    )


def _export_jira(storage, project_path, output):
    """Export to Jira CSV format."""
    from pathlib import Path

    from jinja2 import BaseLoader, Environment

    stories = []
    for summary in storage.list_all():
        try:
            el = storage.read_element(summary.id)
        except Exception:
            continue
        if el.element_type != "user_story":
            continue

        # Extract story details from content (key: value pairs)
        attrs = {}
        if el.content:
            for line in el.content.split("\n"):
                if ":" in line:
                    key, _, val = line.partition(":")
                    attrs[key.strip().lower()] = val.strip()

        # Try to get parent (epic)
        epic = el.parent if el.parent else ""

        # Build acceptance criteria from relationships
        ac_list = []
        if "verified_by" in el.relationships:
            for entry in el.relationships["verified_by"]:
                try:
                    ac = storage.read_element(entry.target)
                    ac_attrs = {}
                    if ac.content:
                        for line in ac.content.split("\n"):
                            if ":" in line:
                                k, _, v = line.partition(":")
                                ac_attrs[k.strip().lower()] = v.strip()
                    ac_list.append({
                        "given": ac_attrs.get("given", ""),
                        "when": ac_attrs.get("when", ""),
                        "then": ac_attrs.get("then", ""),
                    })
                except Exception:
                    pass

        stories.append({
            "title": el.title,
            "story_points": attrs.get("story_points", ""),
            "priority": attrs.get("priority", ""),
            "as_a": attrs.get("as_a", ""),
            "i_want": attrs.get("i_want", ""),
            "so_that": attrs.get("so_that", ""),
            "epic": epic,
            "sprint": attrs.get("sprint", ""),
            "tags": el.tags,
            "acceptance_criteria": ac_list,
        })

    if not stories:
        console.print("[yellow]No user stories found in the specification.[/yellow]")
        return

    # Render CSV template
    template_path = (
        Path(__file__).parent.parent
        / "codegen"
        / "templates"
        / "jira_csv.csv.j2"
    )
    env = Environment(loader=BaseLoader())
    if template_path.exists():
        tmpl = env.from_string(template_path.read_text(encoding="utf-8"))
    else:
        # Fallback inline template
        tmpl = env.from_string(
            "Summary,Description,Story Points,Priority,Epic Link,Acceptance Criteria,Labels,Sprint\n"
            '{% for story in stories %}"{{ story.title }}",'
            '"As a {{ story.as_a }}, I want {{ story.i_want }} so that {{ story.so_that }}",'
            '{{ story.story_points }},{{ story.priority }},{{ story.epic }},"
            '"{% for ac in story.acceptance_criteria %}GIVEN {{ ac.given }} WHEN {{ ac.when }} THEN {{ ac.then }}. {% endfor %}",'
            '{{ story.tags | join(" ") }},{{ story.sprint }}\n'
            "{% endfor %}"
        )

    csv_content = tmpl.render(stories=stories)
    out_path = Path(output) if output else project_path / "backlog.csv"
    out_path.write_text(csv_content, encoding="utf-8")
    console.print(f"[green]Jira CSV saved to {out_path}[/green]")
    console.print(f"  Stories: {len(stories)}")


def _export_compliance(storage, project_path, output):
    """Export to compliance traceability matrix (XLSX)."""
    from pathlib import Path

    from src.export.compliance_exporter import ComplianceExporter

    regulations = []
    controls = []
    evidences = []

    for summary in storage.list_all():
        if summary.aspect != "compliance":
            continue
        try:
            el = storage.read_element(summary.id)
        except Exception:
            continue
        if el.element_type == "regulation":
            regulations.append(el)
        elif el.element_type == "control":
            controls.append(el)
        elif el.element_type == "evidence":
            evidences.append(el)

    if not regulations and not controls:
        console.print(
            "[yellow]No compliance elements found. "
            "Create regulation, control, and evidence elements "
            "in the 'compliance' aspect first.[/yellow]"
        )
        return

    exporter = ComplianceExporter()
    out_path = Path(output) if output else project_path / "compliance_matrix.xlsx"
    exporter.export(regulations, controls, evidences, out_path)

    stats = exporter.compute_coverage(controls, evidences)
    console.print(f"[green]Compliance matrix saved to {out_path}[/green]")
    console.print(
        f"  Regulations: {len(regulations)}, "
        f"Controls: {len(controls)}, "
        f"Evidence: {len(evidences)}"
    )
    console.print(
        f"  Coverage: {stats['coverage_ratio']:.0%} "
        f"({stats['covered_controls']}/{stats['total_controls']} controls have evidence)"
    )

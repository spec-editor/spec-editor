"""CLI subcommand."""

from pathlib import Path

import click
from rich.console import Console

from src.cli.commands import cli, console, _BUILTIN_METHODOLOGIES

from src.ingestion.manager import deprecate_from_file
from src.storage.filesystem import FilesystemStorage
from src.config.settings import AgentConfig, create_provider
from src.agents.questions import QuestionList
from rich.table import Table
import asyncio

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



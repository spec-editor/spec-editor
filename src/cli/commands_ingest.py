"""CLI subcommand."""

import asyncio
import datetime
from pathlib import Path

import click
from rich.console import Console

from src.cli.commands import _BUILTIN_METHODOLOGIES, cli, console
from src.config.settings import AgentConfig, create_provider
from src.ingestion.analyzer import AnalysisReport, Analyzer
from src.ingestion.preprocessor import SourcePreprocessor
from src.ingestion.telegram_hook import TelegramWatcher
from src.storage.filesystem import FilesystemStorage
from src.tracing import implements


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
@cli.command(name="hooks")
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
    help="Path to project directory",
)
@click.option(
    "--transport",
    default="stdio",
    type=click.Choice(["stdio", "http"]),
    help="Transport: stdio (default) or http",
)
@click.option(
    "--port",
    default=8001,
    type=int,
    help="HTTP port (default: 8001)",
)
@click.option(
    "--read-only",
    is_flag=True,
    default=False,
    help="Register only read-only tools (HTTP only)",
)
@click.option(
    "--host",
    default="127.0.0.1",
    type=str,
    help="HTTP host to bind to (default: 127.0.0.1)",
)
@implements("SRC-007")
@implements("MOD-003")
def mcp(
    path: str | None, transport: str, port: int, read_only: bool, host: str
) -> None:
    """Start MCP server for external agents (stdio/json-rpc).

    MCP is configured in your AI agent, not in spec-editor.
    Once the server is running, connect your agent with the config below.
    See README: https://github.com/spec-editor/spec-editor#using-spec-editor-with-ai-coding-assistants

    \b
    Agent config (Zed, Cursor, Claude Desktop, etc.):
    {
      "mcpServers": {
        "spec-editor": {
          "command": "spec-editor",
          "args": ["mcp", "-p", "/path/to/project"]
        }
      }
    }
    """
    import sys

    from src.mcp.server import mcp_server as _server

    proj = path or "."

    if transport == "stdio":
        print(
            "[spec-editor] MCP server starting...",
            "[spec-editor] This is not a command you run directly.",
            "[spec-editor] MCP is configured in your AI agent — connect it with:",
            f'  {{"mcpServers": {{"spec-editor": {{"command": "spec-editor", "args": ["mcp", "-p", "{proj}"]}}}}}}',
            "[spec-editor] See README: https://github.com/spec-editor/spec-editor#using-spec-editor-with-ai-coding-assistants",
            "[spec-editor] Waiting for agent connection... (Ctrl+C to stop)",
            sep="\n",
            file=sys.stderr,
        )
    # HTTP transport prints its own banner (host:port, read-only status)

    _server(
        path=proj if proj != "." else None,
        transport=transport,
        port=port,
        read_only=read_only,
        host=host,
    )


# ---------------------------------------------------------------------------
# Export helper functions
# ---------------------------------------------------------------------------

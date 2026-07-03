"""CLI subcommand."""

import shutil
import tempfile
import webbrowser
from importlib import resources
from pathlib import Path

import click
import frontmatter
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from src.cli.commands import _BUILTIN_METHODOLOGIES, cli, console
from src.context.builder import ContextBuilder
from src.storage.filesystem import FilesystemStorage
from src.view.renderer import MermaidRenderer

# view — render spec graph as interactive HTML/Mermaid, or show element content
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
    "--output",
    "-o",
    default=None,
    help="Output HTML file (default: temp + open browser)",
)
@click.option(
    "--diagram",
    "-d",
    default=None,
    help="Focus graph on element ID and its connections",
)
@click.option(
    "--element",
    "-e",
    default=None,
    help="Show element content by ID in terminal",
)
@click.option(
    "--aspect", "-a", default=None, help="Show all elements in aspect (e.g. modules)"
)
def view(
    path: str,
    output: str | None,
    diagram: str | None,
    element: str | None,
    aspect: str | None,
) -> None:
    """Render the specification as an interactive Mermaid graph in the browser.

    \b
    Full graph:   spec-editor view
    By diagram:   spec-editor view -d ENT-004
    By element:   spec-editor view -e MOD-001
    By aspect:    spec-editor view -a modules
    """
    from pathlib import Path as _Path

    project_path = _Path(path).resolve()

    # --- Element content mode: show element in terminal ---
    if element:
        _show_element_content(project_path, element)
        return

    # --- Graph mode ---
    from src.view.renderer import MermaidRenderer

    renderer = MermaidRenderer()
    out = Path(output) if output else None
    result = renderer.render_html(
        project_path, out, element_id=diagram, aspect_name=aspect
    )
    console.print(f"[green]Spec graph rendered:[/green] {result}")


def _show_element_content(project_path: Path, element_id: str) -> None:
    """Read an element by ID and print its full content to terminal."""
    storage = FilesystemStorage(project_path)
    try:
        el = storage.read_element(element_id)
    except KeyError:
        console.print(f"[red]Element not found:[/red] {element_id}")
        raise SystemExit(1)

    # Header
    console.print()
    console.print(
        Panel(
            f"[bold cyan]{el.id}[/bold cyan]: {el.title}",
            subtitle=f"aspect: {el.aspect}  |  type: {el.element_type}  |  status: {el.status.value}",
            border_style="blue",
        )
    )

    # Metadata table
    meta_table = Table(show_header=False, box=None, padding=(0, 2))
    meta_table.add_column("Key", style="dim")
    meta_table.add_column("Value")
    if el.parent:
        meta_table.add_row("Parent", el.parent)
    if el.children:
        meta_table.add_row("Children", ", ".join(el.children))
    if el.tags:
        meta_table.add_row("Tags", ", ".join(el.tags))
    if el.derived_from:
        meta_table.add_row("Derived from", ", ".join(el.derived_from))
    if el.covered_by:
        meta_table.add_row("Covered by", ", ".join(el.covered_by))
    if el.provenance:
        meta_table.add_row("Source", el.provenance.source)
        meta_table.add_row(
            "Confidence", f"{el.provenance.confidence:.0%}"
        )
    console.print(meta_table)

    # Relationships
    if el.relationships:
        console.print()
        rel_table = Table(title="Relationships", show_header=True)
        rel_table.add_column("Role", style="green")
        rel_table.add_column("Target", style="cyan")
        for rel_type, entries in el.relationships.items():
            for entry in entries:
                rel_table.add_row(entry.role, entry.target)
        console.print(rel_table)

    # Content (markdown body)
    if el.content:
        console.print()
        console.print(Panel(el.content.strip(), title="Content", border_style="dim"))
    else:
        console.print(Panel("[dim](no content)[/dim]", title="Content", border_style="dim"))

    console.print()


# ======================================================================
# demo — quick start: copy bookstore example + open view
# ======================================================================


@cli.command()
@click.option(
    "--output",
    "-o",
    default=None,
    help="Output directory (default: system temp dir / 'spec-editor-demo')",
)
def demo(output: str | None) -> None:
    """Quick demo: see a pre-generated specification without any LLM calls.

    Copies the bookstore example to a temp directory and opens
    the interactive spec graph in your browser. No API key required.
    """
    import shutil
    import sys

    # Find bundled bookstore example
    examples_dir = resources.files("data") / "examples" / "bookstore"
    if not examples_dir.is_dir():
        console.print("[red]Bookstore example not found[/red]")
        raise SystemExit(1)

    # Copy to output dir (short, predictable default)
    if output:
        demo_dir = Path(output).resolve()
    else:
        import tempfile
        demo_dir = Path(tempfile.gettempdir()) / "spec-editor-demo"
    # Fresh start: remove old demo if present
    if demo_dir.exists():
        shutil.rmtree(demo_dir)
    shutil.copytree(examples_dir, demo_dir)

    # Copy methodology.yaml for validate/export/run commands
    builtin_methods = resources.files("data") / "methodologies"
    method_file = builtin_methods / "waterfall.yaml"
    if method_file.exists():
        shutil.copy(method_file, demo_dir / "methodology.yaml")

    # Copy agents.yaml for spec-editor run
    agents_yaml = demo_dir / "agents.yaml"
    if not agents_yaml.exists():
        agents_yaml.write_text("""agents:
  agent_1:
    provider: deepseek
    model: deepseek/deepseek-reasoner
    temperature: 0.7
    max_tokens: 4096
  agent_2:
    provider: deepseek
    model: deepseek/deepseek-reasoner
    temperature: 0.7
    max_tokens: 4096
  orchestrator:
    provider: deepseek
    model: deepseek/deepseek-reasoner
    temperature: 0.3
    max_tokens: 4096
max_rounds: 20
max_time_minutes: 30
""")

    # Use the same binary path the user invoked (works whether they ran
    # `spec-editor` from PATH or `./.venv/bin/spec-editor`)
    spec_bin = sys.argv[0] if sys.argv[0] else "spec-editor"

    console.print(f"[green]Demo project ready:[/green] {demo_dir}")
    console.print()
    console.print("[bold]What's inside:[/bold]")
    console.print(f"  📄 input.md — raw requirements document (team chat style)")
    console.print(f"  📂 aspects/   — structured specification (15 elements)")
    console.print(
        f"     ├── modules/       (5): Catalog, Cart, Checkout, Accounts, Admin"
    )
    console.print(f"     ├── scenarios/     (2): Browse, Checkout")
    console.print(f"     ├── entities/      (4): Book, Order, User, CartItem")
    console.print(f"     └── non_functional/(4): Performance, Capacity, PCI-DSS, GDPR")
    console.print()
    console.print("[bold]Try these next:[/bold]")
    console.print(
        f"  [bold cyan]export SPEC_EDITOR_PROJECT={demo_dir}[/bold cyan]  ← run this first!"
    )
    console.print(f"  {spec_bin} view")
    console.print(f"  {spec_bin} status")
    console.print(f"  {spec_bin} validate")
    console.print(f"  {spec_bin} export")
    console.print()
    console.print("[bold]Want to build specs for your own project?[/bold]")
    console.print("  1. Put requirements docs, chat logs, or PDFs into a folder")
    console.print(f"  2. {spec_bin} init my-project && cd my-project")
    console.print(f"  3. {spec_bin} run       # agents debate, produce aspects/")
    console.print(f"  4. {spec_bin} mcp &     # start MCP server in background")
    console.print("  5. Connect your AI coding agent (Claude Code, Cursor, Zed)")
    console.print("     to the MCP server — with detailed, structured specs,")
    console.print("     the generated code will be far more complete and consistent.")
    console.print()

    # Auto-open view
    from src.view.renderer import MermaidRenderer

    renderer = MermaidRenderer()
    html_path = demo_dir / "spec-graph.html"
    renderer.render_html(demo_dir, html_path)
    console.print(f"[green]Opened spec graph in browser[/green]")


# ======================================================================
# decisions — list/view architecture decision records
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
@click.option("--id", "-i", default=None, help="Show specific decision by ID")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def decisions(path: str, id: str | None, json_output: bool) -> None:
    """List or view architecture decision records (ADR)."""
    from pathlib import Path as _Path

    import frontmatter as _fm

    project = _Path(path).resolve()
    decisions_dir = project / "aspects" / "decisions"

    if not decisions_dir.is_dir():
        console.print("[yellow]No decisions recorded yet.[/yellow]")
        console.print("Agents create decisions automatically during spec-editor run.")
        return

    decision_files = sorted(decisions_dir.glob("*.md"))
    if not decision_files:
        console.print("[yellow]No decision records found.[/yellow]")
        return

    if id:
        # Show specific decision
        df = decisions_dir / f"{id}.md"
        if not df.exists():
            console.print(f"[red]Decision {id} not found[/red]")
            raise SystemExit(1)
        post = _fm.load(str(df))
        console.print(
            f"[bold]{post.metadata.get('id', '?')}: {post.metadata.get('title', '?')}[/bold]"
        )
        console.print(f"  Status: {post.metadata.get('status', 'draft')}")
        console.print(f"  Relates to: {post.metadata.get('relates_to', [])}")
        console.print()
        console.print(post.content)
        return

    if json_output:
        import json as _json

        decisions_list = []
        for df in decision_files:
            post = _fm.load(str(df))
            decisions_list.append(
                {
                    "id": post.metadata.get("id"),
                    "title": post.metadata.get("title"),
                    "status": post.metadata.get("status", "draft"),
                    "relates_to": post.metadata.get("relates_to", []),
                    "content": post.content.strip()[:300],
                }
            )
        console.print(_json.dumps(decisions_list, indent=2, ensure_ascii=False))
        return

    # Table view
    table = Table(title=f"Architecture Decisions ({len(decision_files)})")
    table.add_column("ID", style="cyan")
    table.add_column("Title")
    table.add_column("Status")
    table.add_column("Relates to")

    for df in decision_files:
        post = _fm.load(str(df))
        m = post.metadata
        rels = ", ".join(m.get("relates_to", [])[:3])
        if len(m.get("relates_to", [])) > 3:
            rels += "..."
        table.add_row(
            m.get("id", "?"),
            m.get("title", "?"),
            m.get("status", "draft"),
            rels or "—",
        )

    console.print(table)
    console.print(f"\n[dim]Use --id <ID> to view full decision content[/dim]")


@cli.command(name="context")
@click.option(
    "--path",
    "-p",
    default=".",
    envvar="SPEC_EDITOR_PROJECT",
    type=click.Path(exists=True),
    help="Path to project (or SPEC_EDITOR_PROJECT)",
)
@click.option("--file", "-f", default=None, help="Code file for context")
@click.option("--element", "-e", default=None, help="Spec element ID")
@click.option("--task", "-t", default=None, help="Task description search")
def context_cmd(path, file, element, task):
    """Build spec context for AI coding assistants."""
    from pathlib import Path as _Path

    from src.context.builder import ContextBuilder
    from src.storage.filesystem import FilesystemStorage

    project = _Path(path).resolve()
    storage = FilesystemStorage(project)
    builder = ContextBuilder(storage, project)
    if file:
        ctx = builder.for_file(_Path(file))
    elif element:
        ctx = builder.for_element(element)
    elif task:
        ctx = builder.for_task(task)
    else:
        console.print("[red]Specify --file, --element, or --task[/red]")
        raise SystemExit(1)
    console.print(ctx)

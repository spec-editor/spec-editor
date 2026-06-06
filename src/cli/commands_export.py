"""CLI subcommand."""

import shutil
import tempfile
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.table import Table

from src.cli.commands import _BUILTIN_METHODOLOGIES, cli, console
from src.cli.commands_export_helpers import (
    _export_compliance,
    _export_jira,
    _export_openapi,
    _export_srs,
    _export_trlc,
)
from src.storage.filesystem import FilesystemStorage


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
    default=None,
    type=click.Path(),
    help="SRS template YAML (default: built-in)",
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
        # Resolve SRS template: use provided, project-local, or built-in
        if not template:
            template = project_path / "srs_template.yaml"
            if not template.exists():
                template = Path(__file__).parent.parent.parent / "srs_template.yaml"
        _export_srs(storage, project_path, output, str(template))


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

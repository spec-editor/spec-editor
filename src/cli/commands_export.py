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
    _export_html,
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
    default=".",
    envvar="SPEC_EDITOR_PROJECT",
    type=click.Path(exists=True),
    help="Project path (default: current directory)",
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
    type=click.Choice(["srs", "html", "trlc", "openapi", "jira", "compliance"]),
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
      html     — Styled HTML with relationships (srs_style.j2)
      trlc     — TRLC requirements-as-code (BMW-compatible)
      openapi  — OpenAPI 3.0 YAML (api-first methodology)
      jira     — Jira CSV for sprint backlog import (agile methodology)
      compliance — Compliance traceability matrix XLSX (regulatory methodology)
    """
    from pathlib import Path

    from src.storage.filesystem import FilesystemStorage

    project_path = Path(path).resolve()
    storage = FilesystemStorage(project_path)

    if format == "html":
        _export_html(storage, project_path, output)
    elif format == "trlc":
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
                from importlib import resources

                template = resources.files("data") / "srs_template.yaml"
        _export_srs(storage, project_path, output, str(template))

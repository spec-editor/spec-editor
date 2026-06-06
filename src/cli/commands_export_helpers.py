"""CLI subcommand."""

from pathlib import Path

import click
from rich.console import Console

from src.cli.commands import cli, console, _BUILTIN_METHODOLOGIES

from src.export.pipeline import pipeline_from_config
from src.export.compliance_exporter import ComplianceExporter
from src.storage.filesystem import FilesystemStorage
from jinja2 import Environment, BaseLoader
import re
import tempfile
import shutil

def _export_srs(storage, project_path, output, template):
    """Export to IEEE 830 SRS document."""
    from pathlib import Path

    from src.export.pipeline import pipeline_from_config

    template_path = Path(template) if Path(template).is_absolute() else Path(template)

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
        console.print(f"  [yellow]Duplicates: {data.metadata['duplicates']}[/yellow]")


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
                    ac_list.append(
                        {
                            "given": ac_attrs.get("given", ""),
                            "when": ac_attrs.get("when", ""),
                            "then": ac_attrs.get("then", ""),
                        }
                    )
                except Exception:
                    pass

        stories.append(
            {
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
            }
        )

    if not stories:
        console.print("[yellow]No user stories found in the specification.[/yellow]")
        return

    # Render CSV template
    template_path = (
        Path(__file__).parent.parent / "codegen" / "templates" / "jira_csv.csv.j2"
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
            "{{ story.story_points }},{{ story.priority }},{{ story.epic }}",
            '"{% for ac in story.acceptance_criteria %}GIVEN {{ ac.given }} WHEN {{ ac.when }} THEN {{ ac.then }}. {% endfor %}",'
            '{{ story.tags | join(" ") }},{{ story.sprint }}\n'
            "{% endfor %}",
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



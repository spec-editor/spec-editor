"""CLI subcommand."""

from pathlib import Path

import click
from rich.console import Console

from src.cli.commands import cli, console, _BUILTIN_METHODOLOGIES

from src.cli.commands import _EXAMPLE_TEMPLATE, _README_TEMPLATE
from src.config.methodology import load_methodology
from src.config.settings import AgentConfig, AgentsConfig
import shutil
import yaml

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
@click.option(
    "--with-example",
    is_flag=True,
    help="Include a sample requirements document to try spec-editor run",
)
def init(
    path: str, methodology: str, agents: str | None, with_example: bool = False
) -> None:
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

    sources_raw_dir = project_path / "sources_raw"
    sources_raw_dir.mkdir(exist_ok=True)

    readme_path = source_dir / "readme.md"
    readme_path.write_text(
        _EXAMPLE_TEMPLATE if with_example else _README_TEMPLATE, encoding="utf-8"
    )

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
    if with_example:
        console.print(f"\n  [green]Example source document ready![/green]")
        console.print(f"  Next: cd {project_path} && spec-editor run")
    else:
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



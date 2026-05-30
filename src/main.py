"""CLI entry point — spec-editor."""

import asyncio
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel

load_dotenv()  # load .env with API keys

from src.agents.dialogue import DialogueManager
from src.agents.orchestrator import OrchestratorDecision
from src.agents.spec_agent import SpecAgent
from src.cli.commands import cli as commands_cli
from src.config import get_logger
from src.config.methodology import load_methodology
from src.config.settings import AgentsConfig, Settings, create_provider
from src.storage.filesystem import FilesystemStorage
from src.tracing import implements

console = Console()
logger = get_logger(__name__)

cli = commands_cli


@cli.command()
@click.option(
    "--path", "-p", default=".", type=click.Path(exists=True), help="Project path"
)
@click.option("--max-rounds", "-r", default=None, type=int, help="Round limit")
@click.option("--task", "-t", default=None, help="Task for agents")
@click.option(
    "--verbose", "-v", is_flag=True, help="Verbose log (tool_calls, debug)"
)
def run(path: str, max_rounds: int | None, task: str | None, verbose: bool) -> None:
    """Launch an agent dialogue to refine requirements."""
    project_path = Path(path).resolve()
    method_path = project_path / "methodology.yaml"
    agents_path = project_path / "agents.yaml"

    # ── Configure logging FIRST (before any imports/creations) ──
    import logging

    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(message)s")
    else:
        logging.basicConfig(level=logging.WARNING, format="%(message)s")
        # Suppress litellm WARNINGs about missing modules (bedrock, sagemaker)
        logging.getLogger("litellm").setLevel(logging.ERROR)
        logging.getLogger("LiteLLM").setLevel(logging.ERROR)

    import structlog

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    # ───────────────────────────────────────────────────────────────────

    if not method_path.exists():
        console.print(
            "[red]Error:[/red] methodology.yaml not found. Run 'spec-editor init'."
        )
        raise SystemExit(1)

    method = load_methodology(method_path)
    storage = FilesystemStorage(project_path)

    agents_config = AgentsConfig()
    if agents_path.exists():
        try:
            agents_config = AgentsConfig.from_yaml(agents_path)
        except Exception as exc:
            console.print(
                f"[yellow]Warning:[/yellow] {exc}. Using defaults."
            )

    # Override from .env if set
    settings = Settings()
    agents_config.max_time_minutes = settings.max_time_minutes
    agents_config.max_agents = settings.max_agents

    if max_rounds:
        agents_config.max_rounds = max_rounds

    # Determine the task: explicit, from source/*.md, or default
    initial_task = task
    if not initial_task:
        source_dir = project_path / "source"
        all_elements = storage.list_all()
        if not all_elements and source_dir.is_dir():
            sources = sorted(
                list(source_dir.glob("*.md")) + list(source_dir.glob("*.txt")),
                key=lambda f: f.stat().st_mtime,
            )
            if sources:
                parts = []
                for src_file in sources:
                    parts.append(
                        f"### {src_file.name}\n{src_file.read_text(encoding='utf-8').strip()}"
                    )
                project_desc = "\n\n".join(parts)
                initial_task = (
                    "Develop requirements according to the methodology.\n\n"
                    f"Target system description:\n{project_desc}\n\n"
                    "Start by analysing the description and create a basic requirements structure "
                    "across all aspects of the methodology."
                )
                console.print(
                    f"[dim]Task loaded from source/ ({len(sources)} files)[/dim]\n"
                )
        else:
            initial_task = (
                "Analyse the current state of the specification and determine "
                "whether all methodology aspects are covered. If there are gaps — "
                "propose changes."
            )

    # Create agents via factory
    from src.agents.factory import AgentFactory
    from src.agents.role import AgentRole

    factory = AgentFactory(
        provider=create_provider(agents_config.agent_1),
        storage=storage,
        methodology=method,
        source_dir=str(project_path / "source"),
        max_llm_calls=settings.max_llm_calls,
        token_budget=settings.token_budget,
    )

    agent_1 = factory.create(AgentRole.spec_agent("Agent 1"))
    agent_2 = factory.create(AgentRole.spec_agent("Agent 2"))
    orchestrator = SpecAgent(
        name="orchestrator",
        provider=create_provider(agents_config.orchestrator),
        storage=storage,
        methodology=method,
        source_dir=str(project_path / "source"),
        role=AgentRole.orchestrator(),
    )

    dialogue = DialogueManager(
        agent_1=agent_1,
        agent_2=agent_2,
        orchestrator=orchestrator,
        storage=storage,
        config=agents_config,
        log_dir=project_path,
    )

    console.print("[bold]Starting dialogue[/bold]")
    console.print(f"  Agent 1: {agents_config.agent_1.model}")
    console.print(f"  Agent 2: {agents_config.agent_2.model}")
    console.print(f"  Orchestrator: {agents_config.orchestrator.model}")
    console.print(f"  Round limit: {agents_config.max_rounds}")
    console.print()
    console.print("[dim]Agents are starting specification analysis...[/dim]")

    async def _run():
        return await dialogue.run(
            initial_task=initial_task,
            on_round=_on_round,
            on_orchestrator=_on_orchestrator,
        )

    result = asyncio.run(_run())

    console.print()
    console.print(f"[bold]Dialogue finished: {result.status}[/bold]")
    console.print(f"  Rounds: {result.rounds_completed}")

    if result.final_metrics:
        m = result.final_metrics
        console.print(
            f"  Elements: {m.total_elements}, "
            f"Relationships: {m.total_relationships}, "
            f"Connectivity: {m.connectivity_index:.4f}"
        )
    # Summary by aspects
    all_elements = storage.list_all()
    if all_elements:
        from collections import Counter

        aspect_counts = Counter(s.aspect for s in all_elements)
        console.print("  By aspect:")
        for aspect, count in sorted(aspect_counts.items()):
            console.print(f"    {aspect}: {count}")


def _on_round(round_num: int, msg_a1, msg_a2):
    a1_text = (msg_a1.content or "(tool calls)") if msg_a1 else "..."
    a2_text = (msg_a2.content or "(tool calls)") if msg_a2 else "..."

    layout = Layout()
    layout.split_column(
        Layout(
            Panel(
                a1_text,
                title=f"[bold blue]Agent 1[/bold blue] (round {round_num})",
            )
        ),
        Layout(Panel(a2_text, title=f"[bold green]Agent 2[/bold green]")),
    )
    console.print(layout)


@implements("MOD-001-C3")
def _on_orchestrator(decision: OrchestratorDecision, reason: str):
    color = {
        OrchestratorDecision.CONTINUE: "green",
        OrchestratorDecision.WARNING: "yellow",
        OrchestratorDecision.CONFLICT: "red",
        OrchestratorDecision.COMPLETE: "green",
        OrchestratorDecision.TIMEOUT: "yellow",
    }.get(decision, "white")

    console.print(
        Panel(
            reason or decision.value,
            title=f"[bold {color}]Orchestrator: {decision.value}[/bold {color}]",
            border_style=color,
        )
    )

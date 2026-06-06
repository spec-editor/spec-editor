"""CLI entry point — spec-editor."""

import asyncio
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

load_dotenv()  # load .env with API keys

from src.agents.dialogue import DialogueManager
from src.agents.orchestrator import OrchestratorDecision
from src.agents.spec_agent import SpecAgent
from src.cli.commands import cli as commands_cli
from src.config import get_logger
from src.config.methodology import Methodology, load_methodology
from src.config.settings import AgentsConfig, Settings, create_provider
from src.storage.filesystem import FilesystemStorage
from src.tracing import implements

console = Console()
logger = get_logger(__name__)

cli = commands_cli


def _ensure_sources_ingested(
    project_path: Path,
    storage,
    methodology: Methodology,
    agents_config: AgentsConfig,
) -> int:
    """Ensure the sources aspect has SRC elements.

    Checks sources_raw/ for unprocessed files (preprocessing) and source/
    for raw files (direct SRC creation). Returns the number of new elements.
    """
    sources_raw_dir = project_path / "sources_raw"
    source_dir = project_path / "source"

    # Check existing SRC elements
    src_elements = [e for e in storage.list_all() if e.id.startswith("SRC-")]

    # Case 1: sources_raw/ has unprocessed files → full preprocessing pipeline
    has_raw_files = False
    if sources_raw_dir.is_dir():
        raw_files = [
            f
            for f in sources_raw_dir.iterdir()
            if f.is_file() and not f.name.startswith(("filtered_", "_spam_", "."))
        ]
        has_raw_files = len(raw_files) > 0

    if has_raw_files:
        console.print(
            "[dim]Found unprocessed files in sources_raw/, running ingestion...[/dim]"
        )

        from src.ingestion.analyzer import Analyzer
        from src.ingestion.preprocessor import (
            FactExtractor,
            RequirementClassifier,
            SourcePreprocessor,
        )

        provider = create_provider(agents_config.agent_1)
        classifier = RequirementClassifier(provider)
        extractor = FactExtractor(provider)
        preprocessor = SourcePreprocessor(
            project_path, project_path, classifier, extractor
        )
        processed = preprocessor.process()

        ingestion_dir = project_path / "ingestion"
        analyzer = Analyzer(storage, ingestion_dir)
        report = analyzer.analyze(processed)

        created = len(report.new_requirements)
        if created > 0:
            console.print(
                f"[green]Ingested:[/green] {created} SRC elements from sources_raw/"
            )
        if report.duplicates:
            console.print(f"[dim]{len(report.duplicates)} duplicates skipped[/dim]")
        return created

    # Case 2: No sources_raw, but source/ has files and no SRC elements
    if not src_elements and source_dir.is_dir():
        md_files = sorted(
            list(source_dir.glob("*.md")) + list(source_dir.glob("*.txt")),
            key=lambda f: f.stat().st_mtime,
        )
        if md_files:
            from src.storage.models import Element, ElementStatus, Provenance

            next_id = 1
            for f in md_files:
                try:
                    content = f.read_text(encoding="utf-8")
                except Exception:
                    content = ""
                title = f.stem[:80]
                el = Element(
                    aspect="sources",
                    element_type="source",
                    id=f"SRC-{next_id:03d}",
                    title=title,
                    content=content,
                    status=ElementStatus.DRAFT,
                    provenance=Provenance(source=f.name),
                )
                storage.write_element(el)
                next_id += 1
            console.print(
                f"[green]Created:[/green] {len(md_files)} SRC elements from source/ files"
            )
            return len(md_files)

    return 0


@cli.command()
@click.option(
    "--path", "-p", default=".", type=click.Path(exists=True), help="Project path"
)
@click.option("--max-rounds", "-r", default=None, type=int, help="Round limit")
@click.option("--task", "-t", default=None, help="Task for agents")
@click.option("--verbose", "-v", is_flag=True, help="Verbose log (tool_calls, debug)")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Run agents without writing to the real project",
)
@click.option(
    "--output-dir",
    default=None,
    type=click.Path(),
    help="Directory for dry-run output (default: <project>/.dry_run)",
)
def run(
    path: str,
    max_rounds: int | None,
    task: str | None,
    verbose: bool,
    dry_run: bool,
    output_dir: str | None,
) -> None:
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

    # ── Build agent config ──
    agents_config = AgentsConfig()
    if agents_path.exists():
        try:
            agents_config = AgentsConfig.from_yaml(agents_path)
        except Exception as exc:
            console.print(f"[yellow]Warning:[/yellow] {exc}. Using defaults.")

    # Override from .env if set
    settings = Settings()
    agents_config.max_time_minutes = settings.max_time_minutes
    agents_config.max_agents = settings.max_agents

    if max_rounds:
        agents_config.max_rounds = max_rounds

    # ── Create storage ──
    if dry_run:
        dry_output = Path(output_dir) if output_dir else project_path / ".dry_run"
        dry_output.mkdir(parents=True, exist_ok=True)
        from src.storage.dry_run import DryRunStorage

        storage = DryRunStorage(project_path, dry_output)
        console.print(f"[yellow]Dry-run mode:[/yellow] writing to {dry_output}")
    else:
        storage = FilesystemStorage(project_path)

    # ── Auto-ingestion: ensure sources aspect has SRC elements ──
    _ensure_sources_ingested(project_path, storage, method, agents_config)

    # ── Detect language from source documents ──
    detected_lang = _auto_detect_language(project_path, settings)

    # Reload methodology in detected language for agents
    if detected_lang == "ru":
        ru_path = Path(__file__).parent.parent / "methodologies" / "waterfall-ru.yaml"
        if ru_path.exists():
            method = load_methodology(ru_path)
            console.print("[dim]Using Russian methodology (waterfall-ru.yaml)[/dim]")

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
            # Build a specific task listing which aspects need coverage
            from collections import Counter

            aspect_counts = Counter(s.aspect for s in all_elements)
            method_aspects = {a.name: a.title for a in method.aspects}
            missing = [a for a in method_aspects if a not in aspect_counts]
            existing_str = ", ".join(
                f"{a} ({aspect_counts.get(a, 0)})" for a in method_aspects
            )

            if missing:
                missing_str = "\n".join(
                    f"  - {a} ({method_aspects[a]}) — 0 elements, CREATE FIRST"
                    for a in missing
                )
                initial_task = (
                    f"Current specification: {sum(aspect_counts.values())} elements. "
                    f"Aspects: {existing_str}.\n\n"
                    f"MISSING ASPECTS — create elements for these IMMEDIATELY:\n"
                    f"{missing_str}\n\n"
                    f"For EACH missing aspect, read the source documents and "
                    f"create specification elements with write_element. "
                    f"Do NOT call run_validate or run_metrics until you have "
                    f"created elements for ALL missing aspects. "
                    f"After all aspects have elements, then validate and refine."
                )
            else:
                # Build task from methodology: find under-represented relationship types
                rel_counts = Counter()
                for s in all_elements:
                    try:
                        full = storage.read_element(s.id)
                        for rt in (full.relationships or {}):
                            rel_counts[rt] += len(full.relationships[rt])
                    except Exception:
                        pass

                # Collect all cross-aspect relationship types from methodology
                cross_aspect_rels = {}
                for aspect in method.aspects:
                    for rt in (aspect.relationship_types or []):
                        cross_aspect_rels[rt.name] = {
                            "title": rt.title,
                            "sources": rt.source_aspects,
                            "targets": rt.target_aspects,
                        }

                # Find missing or sparse relationship types
                sparse = []
                for rname, rinfo in cross_aspect_rels.items():
                    count = rel_counts.get(rname, 0)
                    if count == 0:
                        sparse.append((rname, rinfo, "MISSING"))
                    elif rname in ("interacts_with", "applies_to", "implements", "measures", "references") and count < 5:
                        sparse.append((rname, rinfo, f"only {count}"))

                if sparse:
                    lines = []
                    skill_map = {
                        "refines": "scenario_decomposer",
                        "next_step": "scenario_decomposer",
                        "navigates_to": "ui_navigator",
                        "contains": "metrics_linker",
                        "triggers_on": "metrics_linker",
                    }
                    for rname, rinfo, status in sparse:
                        src = ", ".join(rinfo["sources"])
                        tgt = ", ".join(rinfo["targets"])
                        skill = skill_map.get(rname, "")
                        hint = f" (spawn {skill} helper)" if skill else ""
                        lines.append(
                            f"  {rname}: {status} — {src} → {tgt}{hint}"
                        )
                    task_lines = "\n".join(lines)
                    initial_task = (
                        f"All methodology aspects have elements: {existing_str}.\n\n"
                        f"FILL MISSING RELATIONSHIPS. Spawn helpers via request_helper:\n"
                        f"{task_lines}\n\n"
                        f"Delegate work to helpers with request_helper(role=skill_name, task=...). "
                        f"Each helper has a specialized prompt for its relationship type."
                    )
                else:
                    initial_task = (
                        f"All methodology aspects are fully covered "
                        f"({existing_str}). Check for completeness."
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


def _auto_detect_language(project_path: Path, settings: Settings) -> str:
    """Detect source document language and switch prompts accordingly."""
    source_dir = project_path / "source"
    if not source_dir.is_dir():
        return "en"
    # Sample up to 10KB from source files to detect language
    sample = ""
    for f in sorted(source_dir.glob("*.md")) + sorted(source_dir.glob("*.txt")):
        try:
            sample += f.read_text(encoding="utf-8")[:5000]
        except Exception:
            pass
        if len(sample) > 10000:
            break
    if not sample:
        return "en"
    # Count Cyrillic vs Latin characters
    cyrillic = sum(1 for c in sample if "А" <= c <= "я" or c in "Ёё")
    latin = sum(1 for c in sample if c.isalpha() and c.isascii())
    if cyrillic > latin * 0.3:  # >30% Cyrillic → Russian
        from src.agents.prompts import set_prompt_language

        set_prompt_language("ru")
        console.print(
            f"[dim]Language auto-detected: Russian "
            f"({cyrillic} cyrillic / {latin} latin chars)[/dim]"
        )
        return "ru"

    # TODO: Spanish (es) detection
    # Heuristic: ¿ ¡ ñ characters, high ratio of 'a'/'o' word endings
    # Requires: word-frequency analysis or langdetect library
    #
    # TODO: French (fr) detection
    # Heuristic: àâçèéêëîïôûù characters, articles le/la/les/des
    # Requires: word-frequency analysis or langdetect library
    #
    # TODO: German (de) detection
    # Heuristic: ß äöü umlauts, capitalised nouns, long compound words
    # Requires: word-frequency analysis or langdetect library
    #
    # For non-Russian Latin-script documents, prompts default to English.
    # To force a language: SPEC_EDITOR__PROMPT_LANGUAGE=ru|es|fr|de
    return "en"


def _on_round(round_num: int, msg_a1, msg_a2):
    a1_text = (msg_a1.content or "(tool calls)") if msg_a1 else "..."
    a2_text = (msg_a2.content or "(tool calls)") if msg_a2 else "..."

    console.print(
        Panel(a1_text, title=f"[bold blue]Agent 1[/bold blue] (round {round_num})")
    )
    console.print(Panel(a2_text, title=f"[bold green]Agent 2[/bold green]"))


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

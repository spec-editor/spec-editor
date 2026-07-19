"""CLI entry point — spec-editor."""

import asyncio
from pathlib import Path
from typing import Any

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

load_dotenv()  # load .env with API keys

from src.agents.dialogue_manager import DialogueManager
from src.agents.orchestrator import OrchestratorDecision
from src.agents.spec_agent import SpecAgent
from src.cli.commands import cli as commands_cli
from src.config import get_logger
from src.config.methodology import Methodology, format_methodology, load_methodology
from src.config.settings import AgentsConfig, Settings, create_provider
from src.providers.base import LLMProvider
from src.storage.filesystem import FilesystemStorage
from src.tracing import implements

console = Console()
logger = get_logger(__name__)

cli = commands_cli


async def _sync_channels(project_path: Path, direction: str = "in") -> None:
    """Pull from / push to external channels (Planka, Jira, Trello, etc.).

    Called before and after each run.  Reads channel config from
    ``local.yaml`` → ``channels:`` section.

    Uses ``print()`` (not ``console.print()``) so output is captured
    by the ``_TeeWriter`` that mirrors stdout to the run log file.
    """
    local_yaml = project_path / "local.yaml"
    if not local_yaml.exists():
        return

    try:
        import yaml
        config_data = yaml.safe_load(local_yaml.read_text()) or {}
        channels_list = config_data.get("channels", [])
    except Exception:
        return

    if not channels_list:
        return

    from src.channels import create_channel
    from src.channels.models import ChannelConfig

    label = "PULL" if direction == "in" else "PUSH"
    synced = 0
    errors: list[str] = []

    for raw in channels_list:
        if not raw.get("enabled", True):
            continue

        channel_type = raw.get("type", "unknown")
        channel_name = raw.get("name", "")
        channel_id = f"{channel_type}:{channel_name}" if channel_name else channel_type

        try:
            cfg = ChannelConfig(**raw)
            channel = create_channel(cfg)
            if channel is None:
                continue

            if direction == "in":
                # PULL: Planka cards → spec-editor elements
                created, updated = await _pull_cards_to_elements(
                    project_path, channel, cfg, channel_id
                )
                if created or updated:
                    print(
                        f"[ch:{channel_id}] PULL: {created} new, {updated} updated"
                    )
                synced += 1
            else:
                # PUSH: spec-editor elements → Planka cards
                created, updated = await _push_elements_to_cards(
                    project_path, channel, cfg, channel_id
                )
                if created or updated:
                    print(
                        f"[ch:{channel_id}] PUSH: {created} new, {updated} updated"
                    )
                synced += 1

        except Exception as exc:
            errors.append(f"{channel_id}: {exc}")

    if synced > 0 and not errors:
        print(f"Channels {label}: {synced} synced")
    elif errors:
        print(f"Channel {label} errors: {'; '.join(errors[:3])}")


# ═══════════════════════════════════════════════════════════════════
# Bidirectional sync helpers
# ═══════════════════════════════════════════════════════════════════

_TRACKER_ID_KEY = "_tracker_ids"  # element metadata: {channel_type: card_id}

def _get_tracker_id(el: Any, channel_id: str) -> str:
    """Extract Planka card ID from element tags (format: tracker:{channel}:{id})."""
    prefix = f"tracker:{channel_id}:"
    for tag in (el.tags or []):
        if tag.startswith(prefix):
            return tag[len(prefix):]
    return ""

def _set_tracker_id(storage: Any, el_id: str, channel_id: str, card_id: str) -> None:
    """Store Planka card ID as a tag on the element."""
    el = storage.read_element(el_id)
    prefix = f"tracker:{channel_id}:"
    new_tags = [t for t in (el.tags or []) if not t.startswith(prefix)]
    new_tags.append(f"{prefix}{card_id}")
    el.tags = new_tags
    storage.write_element(el)


async def _pull_cards_to_elements(
    project_path: Path,
    channel: Any,
    cfg: Any,
    channel_id: str,
) -> tuple[int, int]:
    """Pull Planka cards → create/update spec-editor elements.

    - New cards → create DRAFT SRC elements
    - Existing cards newer than element → update element title/status
    - Stores card ID in element metadata for dedup.

    Returns (created, updated) counts.
    """
    if cfg.kind.value != "tracker":
        return 0, 0

    cards = await channel.pull()
    if not cards:
        return 0, 0

    from src.storage.filesystem import FilesystemStorage
    from src.storage.models import Element, ElementStatus
    import frontmatter

    storage: FilesystemStorage = FilesystemStorage(project_path)
    all_elements = storage.list_all()
    el_by_tracker_id: dict[str, Any] = {}
    for el in all_elements:
        cid = _get_tracker_id(el, channel_id)
        if cid:
            el_by_tracker_id[cid] = el

    status_map = cfg.mapping.get("status", {})  # lane_name → element_status
    created = 0
    updated = 0

    for card in cards:
        card_id = card.id
        card_title = card.title
        card_status = card.status  # lane name

        if card_id in el_by_tracker_id:
            # Card already linked — check if newer
            existing = el_by_tracker_id[card_id]
            el_mtime = _get_element_mtime(project_path, existing.id)
            card_mtime = _parse_planka_time(card.raw.get("updatedAt", ""))

            if card_mtime and el_mtime and card_mtime > el_mtime:
                # Card is newer — update element
                new_status = status_map.get(card_status, "draft")
                full_el = storage.read_element(existing.id)
                full_el.title = card_title
                full_el.status = ElementStatus(new_status)
                storage.write_element(full_el)
                updated += 1
        else:
            # New card — create SRC element
            new_status = status_map.get(card_status, "draft")
            el_id = f"SRC-PL-{card_id[-8:]}"  # short unique ID
            content = card.description or card_title
            storage.write_element(
                Element(
                    id=el_id,
                    aspect="sources",
                    element_type="source",
                    title=card_title,
                    content=content,
                    status=ElementStatus(new_status),
                    tags=[f"tracker:{channel_id}:{card_id}"],
                )
            )
            created += 1

    return created, updated


async def _push_elements_to_cards(
    project_path: Path,
    channel: Any,
    cfg: Any,
    channel_id: str,
) -> tuple[int, int]:
    """Push spec-editor elements → create/update Planka cards.

    - Elements without linked card → create in Backlog lane
    - Elements newer than linked card → update card title/lane
    - Compares mtime for conflict resolution.

    Returns (created, updated) counts.
    """
    if cfg.kind.value != "tracker":
        return 0, 0

    from src.storage.filesystem import FilesystemStorage

    storage: FilesystemStorage = FilesystemStorage(project_path)
    elements = storage.list_all()
    if not elements:
        return 0, 0

    # Get existing cards for dedup
    cards = await channel.pull()
    card_by_title = {c.title.lower(): c for c in cards if c.title}

    status_map = cfg.mapping.get("status", {})
    reverse_map = {v: k for k, v in status_map.items()}  # element_status → lane

    token = cfg.config.get("token", "")
    board_id = cfg.config.get("board_id", "")
    url = cfg.config.get("url", "").rstrip("/")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    import aiohttp

    # Pre-fetch lanes for efficiency
    lane_by_name: dict[str, str] = {}
    async with aiohttp.ClientSession() as s:
        board_url = f"{url}/api/boards/{board_id}?with=lists"
        async with s.get(board_url, headers=headers) as r:
            if r.status == 200:
                data = await r.json()
                for lst in data.get("included", {}).get("lists", []):
                    name = lst.get("name", "")
                    if name:
                        lane_by_name[name.lower()] = lst["id"]

    created = 0
    updated = 0

    for el in elements[:50]:
        el_title = el.title or el.id
        el_status = (el.status.value if hasattr(el.status, "value") else str(el.status)) if el.status else "draft"
        linked_card_id = _get_tracker_id(el, channel_id)

        # Find matching card: by linked ID first, then by title
        card = None
        if linked_card_id:
            for c in cards:
                if c.id == linked_card_id:
                    card = c
                    break
        if card is None:
            card = card_by_title.get(el_title.lower())

        if card:
            # Card exists — update if element is newer
            el_mtime = _get_element_mtime(project_path, el.id)
            card_mtime = _parse_planka_time(card.raw.get("updatedAt", ""))

            if (not card_mtime) or (el_mtime and el_mtime > card_mtime):
                target_lane = reverse_map.get(el_status, "")
                lane_id = lane_by_name.get((target_lane or "").lower(), "") if target_lane else ""
                el_content = getattr(el, "content", "") or ""
                desc = f"**{el.id}** | {el.aspect} | {el_status}\n\n{el_content[:500]}"
                if lane_id and card.status != target_lane:
                    async with aiohttp.ClientSession() as s2:
                        move_url = f"{url}/api/cards/{card.id}"
                        async with s2.patch(move_url, headers=headers,
                                           json={"listId": lane_id, "description": desc}) as resp:
                            if resp.status in (200, 204):
                                updated += 1
                elif desc:
                    # Update description even if lane hasn't changed
                    async with aiohttp.ClientSession() as s2:
                        patch_url = f"{url}/api/cards/{card.id}"
                        async with s2.patch(patch_url, headers=headers,
                                           json={"description": desc}) as resp:
                            if resp.status in (200, 204):
                                pass  # description updated
                # Store card ID on element for future dedup
                if not linked_card_id:
                    _set_tracker_id(storage, el.id, channel_id, card.id)
        else:
            # No card — create in Backlog
            lane_name = "Backlog"
            lane_id = lane_by_name.get(lane_name.lower(), "")
            if not lane_id and lane_by_name:
                lane_id = list(lane_by_name.values())[0]

            if lane_id:
                async with aiohttp.ClientSession() as s2:
                    create_url = f"{url}/api/lists/{lane_id}/cards"
                    # Build description: element content (first 500 chars) + metadata
                    el_content = getattr(el, "content", "") or ""
                    desc = f"**{el.id}** | {el.aspect} | {el_status}\n\n{el_content[:500]}"
                    payload = {
                        "name": el_title[:200],
                        "type": "project",
                        "position": 1,
                        "description": desc,
                    }
                    async with s2.post(create_url, headers=headers,
                                     json=payload) as resp:
                        if resp.status in (200, 201):
                            data = await resp.json()
                            new_card_id = data.get("item", {}).get("id", "")
                            if new_card_id:
                                _set_tracker_id(storage, el.id, channel_id, new_card_id)
                            created += 1

    return created, updated


def _get_element_mtime(project_path: Path, element_id: str) -> float | None:
    """Get modification time of an element's .md file."""
    md_file = project_path / "aspects" / "sources" / f"{element_id}.md"
    if not md_file.exists():
        # Search recursively
        for f in project_path.rglob(f"{element_id}.md"):
            return f.stat().st_mtime
        return None
    return md_file.stat().st_mtime


def _parse_planka_time(ts: str) -> float | None:
    """Parse Planka ISO timestamp to Unix timestamp."""
    if not ts:
        return None
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


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
                    status=ElementStatus.CONFIRMED,
                    provenance=Provenance(source=f.name),
                )
                storage.write_element(el)
                next_id += 1
            console.print(
                f"[green]Created:[/green] {len(md_files)} SRC elements from source/ files"
            )
            return len(md_files)

    return 0


def _validate_before_run(storage, methodology: Methodology, project_path: Path) -> None:
    """Validate all elements before running agent generation.

    Runs the same validator as 'spec-editor validate' but in strict mode
    (no auto-fix). If errors are found, prints them and aborts the run.
    """
    from src.mcp.validator import validate

    console.print()
    console.print("[bold]Pre-run validation[/bold]")

    # ── Structural validation ──
    report = validate(storage, methodology, fix=False)

    if report.errors:
        console.print()
        console.print(f"[red]Found {len(report.errors)} validation error(s):[/red]")
        for err in report.errors:
            loc = f"{err.element_id}:{err.field}" if err.element_id else "-"
            console.print(f"  [red]✗[/red] [{loc}] {err.message}")
        console.print()
        console.print(
            "[red bold]Cannot proceed with generation.[/red bold] "
            "Fix the errors above, then re-run."
        )
        raise SystemExit(1)

    if report.warnings:
        console.print(f"  [yellow]OK with {len(report.warnings)} warning(s)[/yellow]")
    else:
        console.print(f"  [green]OK — {len(storage.list_all())} elements valid[/green]")

    console.print()


# ── License check ───────────────────────────────────────────────────

@cli.command()
@click.option(
    "--path", "-p", default=".", type=click.Path(exists=True), help="Project path"
)
def sync(path: str) -> None:
    """Bidirectional sync with all configured tracker channels.

    Pulls new cards → creates spec elements.
    Pushes element changes → updates Planka/Trello/Jira cards.
    Safe for polling — idempotent, respects timestamps.
    """
    import asyncio

    project_path = Path(path).resolve()
    asyncio.run(_sync_channels(project_path, direction="in"))
    asyncio.run(_sync_channels(project_path, direction="out"))
    print("Sync complete.")


@cli.command()
def install_vscode() -> None:
    """Install the VSCode extension (if VSCode is installed on this host).

    Finds the bundled .vsix file and runs ``code --install-extension``.
    Safe to run multiple times — uses --force to update.
    """
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    if not shutil.which("code"):
        print("VSCode CLI ('code') not found on PATH. Skipping.")
        return

    # Find the bundled .vsix
    candidates = [
        Path(__file__).resolve().parent.parent / "packages" / "vscode-extension"
        / "spec-editor-vscode-0.1.0.vsix",
        Path(sys.prefix) / "share" / "spec-editor" / "spec-editor-vscode-0.1.0.vsix",
    ]
    vsix = None
    for c in candidates:
        if c.exists():
            vsix = c
            break

    if not vsix:
        print("Bundled .vsix not found. Build it with: cd packages/vscode-extension && npm run build")
        return

    print(f"Installing VSCode extension from: {vsix}")
    result = subprocess.run(
        ["code", "--install-extension", str(vsix), "--force"],
        capture_output=False,
        timeout=30,
    )
    if result.returncode == 0:
        print("VSCode extension installed. Reload VSCode window to activate.")
    else:
        print(f"Install failed (exit code {result.returncode})")


@cli.command()
@click.option(
    "--path", "-p", default=".", type=click.Path(exists=True), help="Project path"
)
@click.option("--max-rounds", "-r", default=None, type=int, help="Round limit")
@click.option("--task", "-t", default=None, help="Task for agents")
@click.option("--verbose", "-v", is_flag=True, help="Verbose log (tool_calls, debug)")
@click.option(
    "--resume",
    is_flag=True,
    help="Resume from last checkpoint instead of starting fresh",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Run agents without writing to the real project",
)
@click.option(
    "--dry-run-incremental",
    "dry_run_incremental",
    is_flag=True,
    help="Preserve previous dry-run output (skip cleanup)",
)
@click.option(
    "--output-dir",
    default=None,
    type=click.Path(),
    help="Directory for dry-run output (default: <project>/.dry_run)",
)
@click.option(
    "--ci",
    default=None,
    type=float,
    help="Minimum connectivity index required to proceed (0.0–∞, e.g. 0.9)",
)
def run(
    path: str,
    max_rounds: int | None,
    task: str | None,
    verbose: bool,
    resume: bool,
    dry_run: bool,
    dry_run_incremental: bool,
    output_dir: str | None,
    ci: float | None,
) -> None:
    """Launch analytics + coding teams to refine requirements and generate code.

    Analytics team (always): AI agents refine the specification across all aspects.
    Coding team (Pro): generates code, runs tests, deploys — if Pro license present.
    """
    import atexit
    import os

    project_path = Path(path).resolve()
    lock_file = project_path / ".spec-editor-running"

    # ── Check for existing running process ──
    if lock_file.exists():
        try:
            old_pid = int(lock_file.read_text().strip())
        except (ValueError, OSError):
            old_pid = 0

        from src.utils import is_process_running
        if old_pid and is_process_running(old_pid):
            console.print(
                f"[red]Error:[/red] Another spec-editor is already running "
                f"(PID {old_pid}).\n"
                f"  Stop it first: [cyan]spec-editor shutdown[/cyan]\n"
                f"  Or remove lock manually: [cyan]rm {lock_file}[/cyan]"
            )
            raise SystemExit(1)
        else:
            # Stale lock from a crashed/killed process — clean it up
            console.print(
                f"[dim]Removing stale lock file (PID {old_pid} is dead)[/dim]"
            )
            lock_file.unlink(missing_ok=True)

    # ── Log file for this run ──
    run_log_file = project_path / ".spec-editor-run.log"
    import sys

    class _TeeWriter:
        """Write to both stdout and log file."""

        def __init__(self, original, log_path):
            self._orig = original
            self._log = open(log_path, "a", encoding="utf-8", buffering=1)

        def write(self, data):
            self._orig.write(data)
            self._log.write(data)

        def flush(self):
            self._orig.flush()
            self._log.flush()

    sys.stdout = _TeeWriter(sys.stdout, run_log_file)
    sys.stderr = _TeeWriter(sys.stderr, run_log_file)

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
    if dry_run or dry_run_incremental:
        dry_output = Path(output_dir) if output_dir else project_path / ".dry_run"
        # Clean previous dry-run output unless incremental mode
        if not dry_run_incremental and dry_output.exists():
            import shutil
            shutil.rmtree(dry_output)
            console.print("[dim]Cleaned previous dry-run output[/dim]")
        dry_output.mkdir(parents=True, exist_ok=True)
        from src.storage.dry_run import DryRunStorage

        storage = DryRunStorage(project_path, dry_output)
        console.print(f"[yellow]Dry-run mode:[/yellow] writing to {dry_output}")
        if dry_run_incremental:
            console.print("[dim]Incremental: preserving previous dry-run elements[/dim]")
    else:
        storage = FilesystemStorage(project_path)

    # ── Auto-ingestion: ensure sources aspect has SRC elements ──
    _ensure_sources_ingested(project_path, storage, method, agents_config)

    # ── Plugin hook: start coding team (Pro) if license present ──
    # The plugin spawns the coding team as a background asyncio task.
    # It never blocks analytics — both teams run in parallel.
    coding_task: Any = None
    try:
        from src.hooks import get_plugins

        for _p in get_plugins():
            try:
                result = _p.on_run(
                    "spec",
                    project_path,
                    storage,
                    method,
                    agents_config,
                    settings,
                    task or "",
                )
                if isinstance(result, bool) and result:
                    # Plugin handled everything — core does not proceed (legacy).
                    return
                # asyncio.Task or similar — coding team background task
                if hasattr(result, "__await__") or hasattr(result, "cancel"):
                    coding_task = result
            except Exception as exc:
                console.print(f"[yellow]Warning:[/yellow] plugin on_run failed: {exc}")
    except ImportError:
        pass

    # ── Core mode: spec only ──

    # ── Pre-run validation: check all elements before agent generation ──
    _validate_before_run(storage, method, project_path)

    # ── Detect language from source documents ──
    detected_lang = _auto_detect_language(project_path, settings)

    # Reload methodology in detected language for agents
    if detected_lang == "ru":
        from src.config._data_path import data_path

        ru_path = data_path("methodologies") / "waterfall-ru.yaml"
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
                        for rt in full.relationships or {}:
                            rel_counts[rt] += len(full.relationships[rt])
                    except Exception:
                        pass

                # Collect all cross-aspect relationship types from methodology
                cross_aspect_rels = {}
                for aspect in method.aspects:
                    for rt in aspect.relationship_types or []:
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
                    elif (
                        rname
                        in (
                            "interacts_with",
                            "applies_to",
                            "implements",
                            "measures",
                            "references",
                        )
                        and count < 5
                    ):
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
                        lines.append(f"  {rname}: {status} — {src} → {tgt}{hint}")
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

    # Choose agent implementation: "loop" (current) or "langgraph" (experimental)
    agent_impl = settings.agent_implementation

    if agent_impl == "langgraph":
        from src.agents.langgraph_agent import LangGraphAgent
        from src.agents.tools import build_all_handlers, get_tool_definitions

        def _make_lg_agent(name: str, role: AgentRole) -> LangGraphAgent:
            tools = get_tool_definitions(writable=role.writable)
            if role._allowed_tools:
                tools = [t for t in tools if t.name in role._allowed_tools]
            handlers = build_all_handlers(
                storage, method, str(project_path / "source"), ci_threshold=ci or 0.7
            )
            prompt = (
                role.prompt.format(methodology_description=format_methodology(method))
                if role.prompt
                else ""
            )
            return LangGraphAgent(
                name=name,
                provider=create_provider(agents_config.agent_1),
                system_prompt=prompt,
                tools=tools,
                tool_handlers=handlers,
                max_llm_calls=settings.max_llm_calls,
                token_budget=settings.token_budget,
            )

    if agent_impl == "langgraph":
        # Core spec mode — Agent 1 creates, Agent 2 links.
        # (Non-core modes: cycle, coding, cycle-graph — handled by plugin on_run hook above.)

        from src.agents.supervisor_graph import SupervisorGraph
        from src.agents.tools import build_all_handlers, get_tool_definitions

        # Agent 1: spec agent (creator)
        role1 = AgentRole.spec_agent("Agent 1")

        tools1 = get_tool_definitions(writable=role1.writable)
        if role1._allowed_tools:
            tools1 = [t for t in tools1 if t.name in role1._allowed_tools]
        handlers1 = build_all_handlers(
            storage, method, str(project_path / "source"), ci_threshold=ci or 0.7
        )
        prompt1 = (
            role1.prompt.format(methodology_description=format_methodology(method))
            if role1.prompt
            else ""
        )

        # Agent 2: linker
        role2 = AgentRole.cross_aspect_agent("Agent 2")
        tools2 = get_tool_definitions(writable=role2.writable)
        if role2._allowed_tools:
            tools2 = [t for t in tools2 if t.name in role2._allowed_tools]
        # Remove read_source_document — linker only reads specification elements
        tools2 = [t for t in tools2 if t.name != "read_source_document"]
        handlers2 = build_all_handlers(
            storage, method, str(project_path / "source"), ci_threshold=ci or 0.7
        )
        # Remove read_source_document handler
        handlers2.pop("read_source_document", None)
        prompt2 = (
            role2.prompt.format(methodology_description=format_methodology(method))
            if role2.prompt
            else ""
        )

        def _provider_factory(agent_name: str) -> LLMProvider:
            if agent_name == "agent_1":
                return create_provider(agents_config.agent_1)
            return create_provider(agents_config.agent_2)

        graph = SupervisorGraph(
            storage=storage,
            config=agents_config,
            provider_factory=_provider_factory,
            agent1_prompt=prompt1,
            agent2_prompt=prompt2,
            agent1_tools=tools1,
            agent2_tools=tools2,
            agent1_handlers=handlers1,
            agent2_handlers=handlers2,
            max_llm_calls=settings.max_llm_calls,
            log_dir=project_path,
            project_path=project_path,
            source_dir=str(project_path / "source"),
            ci_threshold=ci,
        )

        console.print("[bold]Starting multi-agent team (LangGraph supervisor)[/bold]")
        console.print(f"  Agent 1 (creator): {agents_config.agent_1.model}")
        console.print(f"  Agent 2 (linker): {agents_config.agent_2.model}")
        if resume:
            console.print(
                "  Mode: [cyan]RESUME[/cyan] — continuing from last checkpoint"
            )

        # ── Sync external channels (pull) before the run ──
        print("[sync] Pulling from channels...", flush=True)
        asyncio.run(_sync_channels(project_path, direction="in"))
        print("[sync] Pull complete", flush=True)

        console.print()

        # Create lock file so VSCode can track run status
        lock_file.write_text(str(os.getpid()))

        result = asyncio.run(graph.run(initial_task, resume=resume))

        # ── Sync external channels (push) after the run ──
        asyncio.run(_sync_channels(project_path, direction="out"))

        m = result.get("last_metrics", {})
        console.print()
        console.print(f"[bold]Team finished: {result.get('status', 'unknown')}[/bold]")
        console.print(
            f"  Elements: {m.get('total_elements', '?')}, Relationships: {m.get('total_relationships', '?')}"
        )
        console.print(
            f"  Connectivity: {m.get('connectivity_index', '?')}, Orphans: {m.get('orphan_elements', '?')}"
        )
        console.print(
            f"  Cost: ${result.get('agent1_cost', 0) + result.get('agent2_cost', 0):.4f}"
        )

        lock_file.unlink(missing_ok=True)
        return  # Exit early — LangGraph path done

    # ── Loop agent path (original DialogueManager) ──
    agent_1 = factory.create(AgentRole.spec_agent("Agent 1"))
    agent_2 = factory.create(AgentRole.cross_aspect_agent("Agent 2"))
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

    # Create lock file NOW — after all init succeeded, before the long run
    def _cleanup_lock():
        lock_file.unlink(missing_ok=True)

    atexit.register(_cleanup_lock)

    from src.utils import set_signal_handlers
    set_signal_handlers(on_shutdown=lambda *_: (_cleanup_lock(), os._exit(0)))

    lock_file.write_text(str(os.getpid()))

    async def _run():
        return await dialogue.run(
            initial_task=initial_task,
            on_round=_on_round,
            on_orchestrator=_on_orchestrator,
        )

    result = asyncio.run(_run())

    # Remove lock file on successful completion
    lock_file.unlink(missing_ok=True)

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


if __name__ == "__main__":
    cli()

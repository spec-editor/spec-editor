"""CLI command for the cycle — log ingest + bug detection.

Usage:
    spec-editor cycle                   # ingest logs → push bugs to Redis
    spec-editor cycle --watch           # continuous mode (poll every N sec)
    spec-editor cycle --health          # show SRC-BUG-* status

The cycle is a lightweight "sensor" that reads structured logs,
detects anomalies, creates SRC-BUG-* elements, and pushes them
to the Redis message bus.  The PM Agent (spec-editor run --mode cycle-graph)
consumes these bugs and runs the full fix→test→deploy pipeline.

Commands are registered via ``cli_group.add_command()`` in the plugin's cli.py.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from src.config import get_logger
from src.storage.filesystem import FilesystemStorage
from src.tracing import StructuredLogEmitter

logger = get_logger(__name__)
console = Console()


def _log_status_change(project_path: str, element_id: str, old_status: str,
                       new_status: str, trigger: str) -> None:
    """Log every element status transition for auditability."""
    pm = _get_cycle_logger(project_path)
    pm.info("element_status_change",
            element_id=element_id,
            old_status=old_status,
            new_status=new_status,
            trigger=trigger)


def _get_cycle_logger(project_path: str) -> StructuredLogEmitter:
    """Get a structured log emitter for the cycle command.

    Writes to logs/MOD-pm-agent/structured.jsonl alongside other agent logs.
    """
    log_dir = Path(project_path) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return StructuredLogEmitter(
        module_id="MOD-pm-agent",
        scenario_id="SCN-cycle",
        log_dir=str(log_dir),
        auto_element=False,
    )


def _get_storage(project_path: str) -> FilesystemStorage:
    return FilesystemStorage(Path(project_path))


def _get_project_path() -> str:
    cwd = Path.cwd()
    for candidate in [cwd, cwd.parent, cwd.parent.parent]:
        if (candidate / "methodology.yaml").exists() or (candidate / "local.yaml").exists():
            return str(candidate)
    raise click.UsageError(
        "No spec-editor project found. Run from a project directory or use -p /path/to/project."
    )


async def _push_to_pm_agent(project_path: str, bugs_found: int, bug_ids: list[str]) -> bool:
    """Push SRC-BUG-* elements to PM Agent via Redis queue.

    Bugs are created as DRAFT. The analyst phase reviews them in-process,
    then engine.run() dispatches REVIEWED elements to the coding queue.
    We push to project-manager queue so the two-team architecture
    (analyst team ↔ project team) communicates via Redis message bus.
    """
    if not bugs_found:
        return False
    try:
        from src.agents.task_queue import AbstractTaskQueue, get_queue_url

        queue_url = get_queue_url(project_path)
        queue = AbstractTaskQueue.connect(queue_url)
        await queue.connect()
        for bug_id in bug_ids:
            await queue.push(
                "project-manager",
                {"action": "new_bugs_found", "bug_ids": bug_ids,
                 "bugs_found": bugs_found, "project_path": project_path},
            )
        await queue.close()
        console.print(f"[green]Pushed {len(bug_ids)} bug(s) to PM Agent via Redis[/green]")
        return True
    except Exception as exc:
        console.print(f"[yellow]Redis push failed (bugs saved to spec): {exc}[/yellow]")
        return False


async def _ingest_and_push(
    storage: FilesystemStorage,
    project_path: str,
    logs_path: str,
    modules: list[str] | None,
    since: str,
    dry_run: bool,
) -> dict:
    """Collect logs → analyse → create SRC-BUG-* → push to Redis."""
    from spec_editor_cycle.collector import LogCollector
    from spec_editor_cycle.tools import ingest_bugs_tool, run_log_analysis_tool

    pm = _get_cycle_logger(project_path)
    pm.info("cycle_started", logs_path=logs_path, modules=modules, dry_run=dry_run)
    result: dict = {"status": "ok", "logs_collected": 0, "bugs_found": 0, "src_created": []}

    # Phase 1: Collect logs
    try:
        target = str(Path(project_path) / "sources_raw")
        collector = LogCollector(source_dir=logs_path, target_dir=target)
        cr = collector.sync()
        result["logs_collected"] = cr["collected"]
        pm.info("cycle_collect", lines=cr["collected"], modules=cr["modules"])
        console.print(f"[bold]Collect[/bold]: {cr['collected']} lines from {cr['modules']} module(s)")
    except Exception as exc:
        pm.error("cycle_collect_failed", error=str(exc))
        result.setdefault("errors", []).append(f"collect: {exc}")
        return result

    # Phase 2: Analyse
    try:
        ar = await run_log_analysis_tool(
            storage=storage, project_path=project_path, since=since,
            module_id=modules[0] if modules else "",
        )
        result["bugs_found"] = ar.get("bugs_found", 0)
        pm.info("cycle_analyse", bugs_found=result["bugs_found"])
        console.print(f"[bold]Analyse[/bold]: {result['bugs_found']} bug(s) detected")
        for b in ar.get("bugs", []):
            console.print(f"  {b['severity']}: {b['title']} ({b['count']})")
    except Exception as exc:
        pm.error("cycle_analyse_failed", error=str(exc))
        result.setdefault("errors", []).append(f"analyse: {exc}")
        return result

    if result["bugs_found"] == 0:
        pm.info("cycle_complete", status="healthy", bugs_found=0)
        console.print("[green]No bugs — system healthy[/green]")
        return result

    # Phase 3: Ingest (create SRC-BUG-* with DRAFT status)
    # DRAFT elements go to analyst-manager for review before coding.
    try:
        ir = await ingest_bugs_tool(storage=storage, project_path=project_path, dry_run=dry_run)
        result["src_created"] = ir.get("src_created", [])
        pm.info("cycle_ingest", src_created=result["src_created"])
        for bug_id in result["src_created"]:
            _log_status_change(project_path, bug_id, "none", "draft", "ingest")
        console.print(f"[bold]Ingest[/bold]: created {result['src_created']}")
        if result["src_created"]:
            console.print("[dim]  Bugs are DRAFT — analyst-manager reviews before coding agent[/dim]")
    except Exception as exc:
        pm.error("cycle_ingest_failed", error=str(exc))
        result.setdefault("errors", []).append(f"ingest: {exc}")
        return result

    # Hand off to PM Agent via Redis
    pushed = await _push_to_pm_agent(project_path, result["bugs_found"], result["src_created"])
    pm.info("cycle_handoff", pushed=pushed, bug_ids=result["src_created"])
    if pushed:
        pm.info("cycle_pushed_to_redis", count=len(result["src_created"]))

    return result


async def _run_analyst_phase(storage, project_path: str) -> dict:
    """Review DRAFT elements using agent1 → agent2 → orchestrator.

    Calls the SupervisorGraph with analyst-manager role to review
    each DRAFT element and transition it to REVIEWED or DEPRECATED.
    """
    pm = _get_cycle_logger(project_path)
    drafts = [s for s in storage.list_all() if s.status.value == "draft"]
    if not drafts:
        pm.info("analyst_phase_skip", reason="no draft elements")
        return {"reviewed": 0, "deprecated": 0}

    pm.info("analyst_phase_start", draft_count=len(drafts))
    console.print(f"\n[bold]Analyst Phase[/bold] — reviewing {len(drafts)} DRAFT element(s)...")

    try:
        from src.agents.supervisor_graph import SupervisorGraph
        from src.agents.tools import build_all_handlers, get_tool_definitions
        from src.config.methodology import format_methodology
        from src.config.settings import create_provider
        from src.config.engine import MethodologyEngine

        method_path = Path(project_path) / "methodology.yaml"
        method = MethodologyEngine.from_path(method_path) if method_path.exists() else None
        if method is None:
            pm.warning("analyst_no_methodology")
            return {"reviewed": 0, "deprecated": 0}

        # Build provider from project agents.yaml
        agents_path = Path(project_path) / "agents.yaml"
        agents_config = None
        if agents_path.exists():
            from src.config.settings import AgentsConfig
            agents_config = AgentsConfig.from_yaml(agents_path)

        if agents_config is None:
            pm.warning("analyst_no_agents_config")
            return {"reviewed": 0, "deprecated": 0}

        reviewed = 0
        deprecated = 0

        for s in drafts:
            try:
                el = storage.read_element(s.id)
            except Exception:
                continue

            task = (
                f"Review bug {el.id}: {el.title}\n\n"
                f"{el.content[:2000] if el.content else 'No details'}\n\n"
                f"Decide: REVIEWED (real bug, needs coding) or DEPRECATED (false alarm/duplicate)."
            )

            handlers = build_all_handlers(storage, method, str(Path(project_path) / "source"))

            def _provider_factory(name):
                if name == "agent_1":
                    return create_provider(agents_config.agent_1)
                return create_provider(agents_config.agent_2)

            graph = SupervisorGraph(
                storage=storage,
                config=agents_config,
                provider_factory=_provider_factory,
                agent1_prompt="You are an analyst. Review bugs for validity, severity, and affected modules.",
                agent2_prompt="You cross-validate the analyst's findings.",
                agent1_tools=get_tool_definitions(writable=True),
                agent2_tools=get_tool_definitions(writable=True),
                agent1_handlers=handlers,
                agent2_handlers=handlers,
                max_llm_calls=20,
                log_dir=Path(project_path),
                project_path=Path(project_path),
                source_dir=str(Path(project_path) / "source"),
            )

            result = await graph.run(task)

            # Extract the final verdict from the LAST agent message only.
            # Checking str(result) would match system state fields (e.g.,
            # "deprecated" appears in state once ANY element is deprecated).
            old_status = el.status.value
            messages = result.get("messages", [])
            last_msg = ""
            for m in reversed(messages):
                # LangChain messages have .content, dicts have ["content"]
                content = getattr(m, "content", None) or (
                    m.get("content", "") if isinstance(m, dict) else ""
                )
                if content and str(content).strip():
                    last_msg = str(content)
                    break
            verdict_text = last_msg.lower()

            should_deprecate = (
                "deprecat" in verdict_text
                or "false alarm" in verdict_text
                or "not a bug" in verdict_text
                or "duplicate" in verdict_text
            )
            if should_deprecate:
                el.status = type(el.status).DEPRECATED
                deprecated += 1
                _log_status_change(project_path, el.id, old_status, "deprecated", "analyst")
                console.print(f"  [dim]{el.id}: DRAFT → DEPRECATED[/dim]")
            else:
                el.status = type(el.status).REVIEWED
                reviewed += 1
                _log_status_change(project_path, el.id, old_status, "reviewed", "analyst")
                console.print(f"  [green]{el.id}: DRAFT → REVIEWED[/green]")

            storage.write_element(el)

        pm.info("analyst_phase_done", reviewed=reviewed, deprecated=deprecated)
        return {"reviewed": reviewed, "deprecated": deprecated}

    except ImportError as exc:
        pm.warning("analyst_unavailable", error=str(exc))
        console.print(f"[yellow]Analyst agents not available: {exc}[/yellow]")
        return {"reviewed": 0, "deprecated": 0}


async def _run_architect_phase(storage, project_path: str) -> dict:
    """Assign implementation decisions to REVIEWED elements.

    For each REVIEWED MOD-*/ENT-*/NFR-* element, creates or updates
    an IMP-* element with implementation_architect decisions
    (structure, domain style, template, layer, ports, adapters).

    Uses a lightweight single-pass LLM call per element — no full
    SupervisorGraph needed since decisions are deterministic.
    """
    pm = _get_cycle_logger(project_path)

    # Find REVIEWED elements that need architect decisions
    reviewed = [
        s for s in storage.list_all()
        if s.status.value == "reviewed"
        and any(s.id.startswith(p) for p in ("MOD-", "ENT-", "NFR-"))
    ]
    if not reviewed:
        pm.info("architect_phase_skip", reason="no reviewed code elements")
        return {"decisions_made": 0}

    pm.info("architect_phase_start", candidate_count=len(reviewed))
    console.print(f"\n[bold]Architect Phase[/bold] — assigning implementation plans for {len(reviewed)} element(s)...")

    try:
        from src.implementation.engine import ImplementationEngine

        impl_engine = ImplementationEngine(project_path)
    except Exception as exc:
        pm.warning("architect_engine_unavailable", error=str(exc))
        return {"decisions_made": 0}

    decisions_made = 0

    for s in reviewed:
        try:
            el = storage.read_element(s.id)
        except Exception:
            continue

        # Check if an IMP element already exists for this requirement
        existing_imp = None
        for child_id in (el.children or []):
            if child_id.startswith("IMP-"):
                try:
                    existing_imp = storage.read_element(child_id)
                    break
                except Exception:
                    pass

        if existing_imp is None:
            # Search for IMP elements that implement this requirement
            all_imps = [si for si in storage.list_all() if si.id.startswith("IMP-")]
            for imp_summary in all_imps:
                try:
                    imp = storage.read_element(imp_summary.id)
                    if imp.parent == el.id or (
                        imp.relationships
                        and "implements" in imp.relationships
                        and any(
                            r.target == el.id
                            for r in imp.relationships.get("implements", [])
                        )
                    ):
                        existing_imp = imp
                        break
                except Exception:
                    pass

        # Build architect decisions using the engine's context
        ctx = impl_engine.get_generation_context(el)

        # Compute sensible template + layer defaults based on element type
        template_name = _default_template(el)
        target_layer = _default_layer(el, ctx["pattern"])

        if existing_imp is not None:
            # Update existing IMP element with architect decisions
            existing_imp.implementation_architect = {
                "structure": ctx["pattern"],
                "domain_style": "ddd",
                "template": template_name,
                "layer": target_layer,
            }
            storage.write_element(existing_imp)
            decisions_made += 1
            pm.info("architect_updated_imp",
                    element_id=el.id, imp_id=existing_imp.id,
                    pattern=ctx["pattern"])
            console.print(f"  [cyan]{el.id}[/cyan] → updated {existing_imp.id}")
        else:
            # Create new IMP element
            from src.storage.models import Element, ElementStatus, RelationshipEntry

            imp_id = f"IMP-{el.id.replace('MOD-', '').replace('ENT-', '').replace('NFR-', '')}"
            # Ensure unique ID
            existing_ids = {si.id for si in storage.list_all() if si.id.startswith("IMP-")}
            counter = 1
            base_id = imp_id
            while imp_id in existing_ids:
                counter += 1
                imp_id = f"{base_id}-{counter}"

            imp = Element(
                aspect="implementation",
                element_type="code_artifact",
                id=imp_id,
                title=f"Implementation plan for {el.id} — {el.title}",
                status=ElementStatus.REVIEWED,
                parent=el.id,
                relationships={
                    "implements": [
                        RelationshipEntry(role="implements", target=el.id)
                    ]
                },
                implementation_architect={
                    "structure": ctx["pattern"],
                    "domain_style": "ddd",
                    "template": template_name,
                    "layer": target_layer,
                },
                content=(
                    f"Implementation plan for {el.id}.\n\n"
                    f"Pattern: {ctx['pattern']}.\n"
                    f"Generated by implementation_architect agent.\n"
                ),
            )
            storage.write_element(imp)
            decisions_made += 1
            pm.info("architect_created_imp",
                    element_id=el.id, imp_id=imp_id,
                    pattern=ctx["pattern"])
            console.print(f"  [green]{el.id}[/green] → created {imp_id}")

    pm.info("architect_phase_done", decisions_made=decisions_made)

    # Notify if decisions were made
    if decisions_made > 0:
        try:
            from src.notifiers import create_notifier
            notifier = create_notifier(project_path)
            notifier.send(
                f"Implementation plans created for {decisions_made} element(s)",
                channel="architect",
                title="Implementation Architect",
                severity="info",
                metadata={"decisions_made": decisions_made, "project": project_path},
            )
        except Exception:
            pass

    return {"decisions_made": decisions_made}


def _default_template(el: Any) -> str:
    """Compute a sensible default template name based on element type."""
    eid = getattr(el, "id", "")
    etype = getattr(el, "element_type", "")
    aspect = getattr(el, "aspect", "")

    if eid.startswith("MOD-"):
        return "rest_service"  # most common
    elif eid.startswith("ENT-"):
        return "entity"
    elif eid.startswith("NFR-"):
        return "middleware"
    return ""


def _default_layer(el: Any, pattern_name: str) -> str:
    """Compute a sensible default layer based on element type and pattern."""
    eid = getattr(el, "id", "")
    etype = getattr(el, "element_type", "")

    if eid.startswith("MOD-"):
        return "domain"
    elif eid.startswith("ENT-"):
        return "domain"
    elif eid.startswith("NFR-"):
        return "adapters"  # NFRs are typically middleware in adapters
    return ""


async def _coding_team_loop(
    storage: Any,
    project_path: str,
) -> dict:
    """Coding Team: consume tasks from Redis queue → OpenCode → confirm.

    Runs in parallel with the product team. Polls the Redis coding queue
    for generate/fix tasks, calls OpenCode to implement them, and confirms
    elements on success.

    Exits naturally when the queue stays empty — no external signal needed.
    Teams communicate via Redis only.
    """
    import asyncio

    pm = _get_cycle_logger(project_path)
    totals = {"generated": 0, "fixed": 0, "failed": 0}

    # Connect to Redis queue
    from src.agents.task_queue import AbstractTaskQueue, get_queue_url

    queue_url = get_queue_url(project_path)
    if not queue_url or "redis" not in queue_url.lower():
        pm.warning("coding_team_no_redis", queue_url=queue_url)
        console.print("[yellow]Coding team: no Redis URL — skipping[/yellow]")
        return totals

    try:
        queue = AbstractTaskQueue.connect(queue_url)
        await queue.connect()
    except Exception as exc:
        pm.warning("coding_team_queue_failed", error=str(exc))
        console.print(f"[yellow]Coding team: queue unavailable — {exc}[/yellow]")
        return totals

    pm.info("coding_team_started", queue_url=queue_url)
    console.print(f"[bold cyan]Coding Team[/bold cyan] — consuming from {queue_url}")

    # OpenCode provider
    try:
        from spec_editor_cycle.providers import OpenCodeProvider
        provider = OpenCodeProvider(project_path)
    except Exception as exc:
        pm.warning("coding_team_opencode_failed", error=str(exc))
        await queue.close()
        return totals

    processed = 0
    max_tasks = 150  # safety limit

    try:
        import asyncio as _asyncio

        from src.agents.constants import CODING, QUEUE_CODING

        async def _consume():
            nonlocal processed
            idle_polls = 0
            max_idle_polls = 5  # exit after 5 consecutive empty polls (~10s)
            async for task in queue.subscribe(QUEUE_CODING, consumer_id="cycle-loop"):
                # Check if queue is drained.
                # pending() returns list[Task]; exceptions reset to empty list
                # so len() works correctly in all cases.
                try:
                    pending_list = await queue.pending(QUEUE_CODING)
                except Exception:
                    pending_list = []
                if len(pending_list) == 0:
                    idle_polls += 1
                    if idle_polls >= max_idle_polls:
                        pm.info("coding_team_queue_drained", idle_polls=idle_polls)
                        break
                    await _asyncio.sleep(2)
                    continue
                idle_polls = 0  # reset on activity

                if processed >= max_tasks:
                    break

                payload = task.payload
                action = payload.get("action", "generate")
                element_id = payload.get("element_id", payload.get("bug_id", "unknown"))

                if action == "fix":
                    task_desc = f"Fix bug {element_id}: {payload.get('title', '')}"
                else:
                    task_desc = f"Generate code for {element_id}: {payload.get('title', '')}"

                pm.info("coding_task_started", task_id=task.task_id, element_id=element_id, action=action)
                console.print(f"  [cyan]Coding:[/cyan] {action} {element_id}...")

                try:
                    result = await provider.run(
                        storage=storage,
                        task=task_desc,
                        model=payload.get("model") or os.environ.get(
                            "SPEC_EDITOR__AGENT_1__MODEL", "deepseek/deepseek-reasoner"
                        ),
                    )
                except Exception as exc:
                    pm.error("coding_task_failed", task_id=task.task_id, error=str(exc))
                    totals["failed"] += 1
                    from src.agents.task_queue import TaskResult
                    await queue.ack(task, TaskResult(task.task_id, CODING, "failed", {"error": str(exc)}))
                    continue

                if result.get("status") == "ok":
                    try:
                        el = storage.read_element(element_id)
                        from src.storage.models import ElementStatus
                        el.status = ElementStatus.CONFIRMED
                        storage.write_element(el)
                        _log_status_change(project_path, element_id, "reviewed", "confirmed", "coding_team")
                        pm.info("coding_element_confirmed", element_id=element_id)
                    except Exception:
                        pass
                    totals["generated" if action == "generate" else "fixed"] += 1
                    from src.agents.task_queue import TaskResult
                    await queue.ack(task, TaskResult(task.task_id, CODING, "ok"))
                    console.print(f"    [green]✓ {element_id} confirmed[/green]")
                else:
                    totals["failed"] += 1
                    from src.agents.task_queue import TaskResult
                    await queue.ack(task, TaskResult(task.task_id, CODING, "failed", {"error": result.get("error", "")}))
                    console.print(f"    [red]✗ {element_id} failed: {result.get('error', 'unknown')[:80]}[/red]")

                processed += 1

        # Run until queue drained or max_tasks reached
        await _consume()
    except Exception as exc:
        pm.error("coding_team_error", error=str(exc))
    finally:
        await queue.close()

    pm.info("coding_team_done", **totals)
    console.print(f"[bold cyan]Coding Team done:[/bold cyan] {totals['generated']} generated, {totals['fixed']} fixed, {totals['failed']} failed")
    return totals


def _ensure_project_scaffold(project_path: str, pm: Any) -> None:
    """Auto-scaffold project directories + architecture tests if missing.

    Called at the start of the cycle loop to ensure the project has
    the correct directory structure for code generation.
    Idempotent — does nothing if scaffolding already exists.
    """
    from pathlib import Path

    proj = Path(project_path)
    domain_dir = proj / "src" / "domain"

    # ── Ensure opencode.json with snapshot disabled ──
    _ensure_opencode_config(proj, pm)

    if domain_dir.exists():
        return  # Already scaffolded

    try:
        from src.implementation.engine import ImplementationEngine

        engine = ImplementationEngine(project_path)
        result = engine.initialize_project()
        dirs = result.get("dirs_created", [])
        files = result.get("files_written", [])
        if dirs or files:
            pm.info(
                "auto_scaffold_done",
                dirs=len(dirs),
                files=len(files),
            )
    except Exception as exc:
        pm.warning("auto_scaffold_failed", error=str(exc))


def _ensure_opencode_config(proj: "Path", pm: Any) -> None:
    """Ensure opencode.json exists with snapshot disabled.

    OpenCode's snapshot system uses git-add internally, which causes
    an infinite retry loop on files that aren't git-tracked. Disabling
    snapshots removes this behavior entirely.
    """
    import json

    config_path = proj / "opencode.json"
    if config_path.exists():
        return  # Already configured

    config = {
        "$schema": "https://opencode.ai/config.json",
        "snapshot": False,
    }
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    pm.info("opencode_config_created", path=str(config_path))


def _recover_stale_dispatched(storage, project_path: str) -> int:
    """Reset stale 'dispatched' tags that would block the next dispatch cycle.

    A "dispatched" tag marks an element's module as busy, preventing other
    elements in the same module from being dispatched.  If a previous cycle
    crashed or the coding team never processed those tasks, the tags remain
    and block ALL future work.

    This recovery runs on every ``cycle`` start and resets dispatched tags
    on elements whose status indicates they are no longer being worked on
    (confirmed, deprecated, or reviewed with no active Redis task).
    """
    pm = _get_cycle_logger(project_path)
    cleaned = 0

    # Collect active task IDs from Redis coding queue (sync, no async needed).
    active_tasks: set[str] = set()
    try:
        from src.agents.task_queue import get_queue_url
        import redis as _sync_redis

        queue_url = get_queue_url(project_path)
        base_url = queue_url.split("?")[0]
        r = _sync_redis.from_url(base_url, socket_connect_timeout=2)
        # XPENDING_RANGE returns actual pending message dicts (not summary)
        stream = "prompt3:tasks:coding"
        group = "prompt3:group-coding"
        try:
            pending_entries = r.xpending_range(stream, group, min="-", max="+", count=100)
            for entry in pending_entries:
                msg_id = entry.get("message_id", "")
                if not msg_id:
                    continue
                # Read the message body to extract element_id
                msgs = r.xrange(stream, min=msg_id, max=msg_id, count=1)
                for _, data in msgs:
                    payload_str = data.get("payload", "{}")
                    try:
                        import json as _json
                        payload = _json.loads(payload_str)
                    except Exception:
                        payload = {}
                    eid = payload.get("element_id", "")
                    if eid:
                        active_tasks.add(eid)
        except Exception:
            pass
        r.close()
    except Exception:
        pass

    for s in storage.list_all():
        try:
            el = storage.read_element(s.id)
        except Exception:
            continue
        tags = getattr(el, "tags", []) or []
        if "dispatched" not in tags:
            continue

        status_val = el.status.value if hasattr(el.status, "value") else str(el.status)

        # Never reset tags on elements that are genuinely in-flight:
        # their task is still pending in the Redis coding queue.
        if el.id in active_tasks:
            continue

        # Always reset on confirmed/deprecated — these are done.
        # Also reset on reviewed elements whose dispatch is stale
        # (no active Redis task).
        if status_val in ("confirmed", "deprecated"):
            el.tags = [t for t in tags if t != "dispatched"]
            storage.write_element(el)
            cleaned += 1
        elif status_val == "reviewed" and el.id not in active_tasks:
            el.tags = [t for t in tags if t != "dispatched"]
            storage.write_element(el)
            cleaned += 1

    if cleaned:
        pm.info("recovery_reset_dispatched", count=cleaned)
        console.print(f"[dim]Recovery: reset {cleaned} stale dispatched tag(s)[/dim]")
    return cleaned


def _clear_pyc_cache(project_path: str) -> int:
    """Remove stale .pyc files and __pycache__ dirs from the project.

    Prevents import shadowing bugs where a renamed/deleted .py module
    still loads via its cached bytecode.  Also clears the spec-editor
    cycle plugin cache for the same reason.

    Returns count of directories removed.
    """
    from pathlib import Path
    import shutil

    proj = Path(project_path)
    count = 0

    for root_dir in [proj, Path(__file__).resolve().parent.parent.parent.parent]:
        for pycache in root_dir.rglob("__pycache__"):
            try:
                shutil.rmtree(pycache, ignore_errors=True)
                count += 1
            except Exception:
                pass
        for pyc in root_dir.rglob("*.pyc"):
            try:
                pyc.unlink(missing_ok=True)
                count += 1
            except Exception:
                pass

    if count:
        console.print(f"[dim]Cache: removed {count} stale .pyc/__pycache__ entries[/dim]")
    return count


# ──────────────────────────────────────────────────────────────────
# Singleton lock — one cycle per project
# ──────────────────────────────────────────────────────────────────

_LOCK_FILE = ".spec-editor-cycle.lock"


def _acquire_lock(project_path: str) -> None:
    """Acquire a per-project lock to prevent duplicate cycle instances.

    Writes the current PID to a lock file.  If a lock already exists
    and the owning process is still alive, refuses to start.
    """
    import os as _os

    lock_path = Path(project_path) / _LOCK_FILE
    if lock_path.exists():
        try:
            old_pid = int(lock_path.read_text().strip())
            # Check if the old process is still alive
            _os.kill(old_pid, 0)  # signal 0 = existence check
            console.print(
                f"[red]Another cycle is already running on this project "
                f"(PID {old_pid}).[/red]\n"
                f"[dim]Stop it first or delete {lock_path}[/dim]"
            )
            raise SystemExit(1)
        except (ValueError, ProcessLookupError):
            # Stale lock: PID doesn't exist or file is corrupt
            console.print(f"[dim]Removing stale lock file (PID no longer alive)[/dim]")
            lock_path.unlink(missing_ok=True)
        except SystemExit:
            raise
        except Exception:
            pass

    lock_path.write_text(str(_os.getpid()))
    console.print(f"[dim]Lock acquired ({_os.getpid()})[/dim]")


def _release_lock(project_path: str) -> None:
    """Release the per-project lock."""
    lock_path = Path(project_path) / _LOCK_FILE
    lock_path.unlink(missing_ok=True)


async def _run_full_loop(project_path: str, single_pass: bool = False) -> dict:
    """Run the full cycle loop: ingest → analyst → PM Agent → repeat.

    Loops until convergence (2 idle iterations) unless single_pass=True.
    """
    import time as _time
    from spec_editor_cycle.engine import WorkflowEngine
    from src.storage.filesystem import FilesystemStorage

    pm = _get_cycle_logger(project_path)
    storage = FilesystemStorage(Path(project_path))
    engine = WorkflowEngine(storage=storage, project_path=project_path, provider="opencode")

    start_ts = _time.time()
    iteration = 0
    idle_streak = 0
    max_iterations = 8
    totals = {"bugs_found": 0, "bugs_created": 0, "reviewed": 0, "deprecated": 0,
              "dispatched": 0, "bugs_fixed": 0, "steps": 0, "deployed": False}

    pm.info("full_loop_start", single_pass=single_pass, max_iterations=max_iterations)

    # ── Recovery: reset stale dispatched tags from previous crashed runs ──
    _recover_stale_dispatched(storage, project_path)

    # ── Auto-scaffold: ensure project has src/ dirs + arch tests ──
    _ensure_project_scaffold(project_path, pm)

    # ── Spawn all teams as background workers via Redis ──
    # All agents use the same AgentWorker.run_queue() pattern:
    # subscribe to tasks:{role} queue, process tasks, exit on drain.
    import asyncio as _asyncio
    from src.agents.persistent_agent import AgentWorker

    workers: list[AgentWorker] = []
    worker_tasks: list[_asyncio.Task] = []

    from src.agents.constants import ALL_ROLES

    for role in ALL_ROLES:
        worker = AgentWorker(role=role, project_path=Path(project_path))
        workers.append(worker)
        t = _asyncio.create_task(worker.run())
        worker_tasks.append(t)
        pm.info(f"{role.replace('-', '_')}_spawned", role=role)

    console.print(
        "\n[bold green]Started background agent queues:[/bold green] "
        "coding, project-manager, analyst-manager, tester, devops\n"
        "[dim]To stop all agents — use [bold]spec-editor shutdown[/bold][/dim]\n"
    )

    while iteration < max_iterations:
        iteration += 1
        iter_start = _time.time()

        if single_pass and iteration > 1:
            break

        # ── Set iteration ID for structured log tagging ──
        from src.tracing import set_iteration_id
        set_iteration_id(str(iteration))

        pm.info("loop_iteration_start", iteration=iteration, idle_streak=idle_streak)
        console.print(f"\n[bold]═══ Iteration {iteration}{'/'+str(max_iterations) if not single_pass else ''} ═══[/bold]")

        # ── Ingest: read logs, create bugs ──
        ingest_result = await _ingest_and_push(
            storage, project_path, "logs/", None, "", False
        )
        bugs_found = ingest_result.get("bugs_found", 0)
        bugs_created = len(ingest_result.get("src_created", []))
        totals["bugs_found"] += bugs_found
        totals["bugs_created"] += bugs_created

        # ── Analyst: review DRAFT → REVIEWED/DEPRECATED ──
        # Skip if no new DRAFT elements appeared since last iteration
        draft_count = len([s for s in storage.list_all() if s.status.value == "draft"])
        prev_draft_count = totals.get("_prev_draft_count", -1)
        if draft_count > 0 and draft_count != prev_draft_count:
            analyst_result = await _run_analyst_phase(storage, project_path)
            reviewed = analyst_result.get("reviewed", 0)
            deprecated = analyst_result.get("deprecated", 0)
        else:
            if draft_count == 0:
                pm.info("analyst_phase_skip", reason="no draft elements")
            else:
                pm.info("analyst_phase_skip", reason=f"no new drafts ({draft_count} unchanged)")
            analyst_result = {"reviewed": 0, "deprecated": 0}
            reviewed, deprecated = 0, 0
        totals["reviewed"] += reviewed
        totals["deprecated"] += deprecated
        totals["_prev_draft_count"] = draft_count  # track for next iteration

        # ── Architect: assign implementation decisions to REVIEWED elements ──
        architect_result = await _run_architect_phase(storage, project_path)
        arch_decisions = architect_result.get("decisions_made", 0)
        totals["architect_decisions"] = totals.get("architect_decisions", 0) + arch_decisions

        # ── PM dispatch runs in background via AgentWorker(role="project-manager")
        #     The worker's _proactive_scan_loop dispatches reviewed elements
        #     every 60s to the coding queue.  We still run the workflow for
        #     health checks, deploy, and escalation steps.
        pm.info("pm_agent_started", iteration=iteration,
                bugs_found=bugs_found, reviewed=reviewed)
        wf_result = await engine.run(iterations=1)

        bugs_fixed = wf_result.get("bugs_fixed", 0)
        dispatched = wf_result.get("dispatched", 0)
        steps = len(wf_result.get("steps_completed", []))
        wf_errors = wf_result.get("errors", [])
        totals["bugs_fixed"] += bugs_fixed
        totals["dispatched"] += dispatched
        totals["steps"] += steps

        pm.info("loop_iteration_done", iteration=iteration,
                bugs_found=bugs_found, bugs_created=bugs_created,
                reviewed=reviewed, deprecated=deprecated,
                dispatched=dispatched, bugs_fixed=bugs_fixed, steps=steps,
                errors=len(wf_errors), elapsed_ms=int((_time.time() - iter_start) * 1000))

        console.print(f"[bold]Iteration {iteration} done[/bold]: "
                      f"{bugs_found} found, {reviewed} reviewed, "
                      f"{dispatched} dispatched, {bugs_fixed} fixed, {steps} steps")

        # ── Convergence check ──
        if bugs_found == 0 and reviewed == 0 and bugs_fixed == 0 and dispatched == 0:
            idle_streak += 1
            pm.info("loop_idle", iteration=iteration, idle_streak=idle_streak)
            console.print(f"[dim]Idle streak: {idle_streak}/2[/dim]")
            if idle_streak >= 2:
                pm.info("loop_converged", iteration=iteration, reason="2 idle iterations")
                console.print("[green]Converged — nothing to do.[/green]")
                break
        else:
            idle_streak = 0

        if single_pass:
            break

        # ── WAIT: poll Redis, process immediately if queue non-empty ──
        try:
            from src.agents.task_queue import AbstractTaskQueue, get_queue_url
            queue_url = get_queue_url(project_path)
            queue = AbstractTaskQueue.connect(queue_url)
            await queue.connect()
            pending = await queue.pending("coding")
            await queue.close()
            pm.info("loop_wait_poll", pending_coding=pending)
            if pending > 0:
                console.print(f"[yellow]{pending} pending coding tasks — continuing immediately[/yellow]")
                continue
        except Exception as exc:
            pm.warning("loop_wait_poll_failed", error=str(exc))

        console.print(f"[dim]Waiting 10s before next iteration...[/dim]")
        await asyncio.sleep(10)

    elapsed_s = int(_time.time() - start_ts)

    # ── Wait for all workers to drain their queues naturally ──
    # All agents use the same pattern: exit when queue stays empty.
    for worker, t in zip(workers, worker_tasks):
        role = worker.role
        try:
            await t
            pm.info(f"{role.replace('-', '_')}_done")
        except Exception as exc:
            pm.warning(f"{role.replace('-', '_')}_shutdown_error", error=str(exc))

    pm.info("full_loop_complete",
            iterations=iteration, idle_streak=idle_streak,
            elapsed_s=elapsed_s,
            total_bugs_found=totals["bugs_found"],
            total_bugs_created=totals["bugs_created"],
            total_reviewed=totals["reviewed"],
            total_deprecated=totals["deprecated"],
            total_dispatched=totals["dispatched"],
            total_bugs_fixed=totals["bugs_fixed"],
            total_steps=totals["steps"])
    return {"status": "ok", "iterations": iteration, "idle_streak": idle_streak,
            "elapsed_s": elapsed_s, "totals": totals}


# ======================================================================
# cycle CLI command
# ======================================================================


@click.command("log-clear")
@click.option("-p", "--project", "project_path", default=None, help="Path to spec-editor project.")
def log_clear_cmd(project_path: str | None) -> None:
    """Clear all cycle logs (logs/* directories)."""
    import shutil
    resolved = project_path or _get_project_path()
    log_dir = Path(resolved) / "logs"
    if log_dir.exists():
        count = len(list(log_dir.iterdir()))
        shutil.rmtree(log_dir)
        log_dir.mkdir()
        console.print(f"[green]Cleared {count} log file(s) from {log_dir}[/green]")
    else:
        console.print(f"[dim]No logs directory at {log_dir}[/dim]")


@click.command("cycle")
@click.option("-p", "--project", "project_path", default=None, help="Path to spec-editor project.")
@click.option("--logs", "logs_path", default="logs/", help="Path to application logs directory.")
@click.option("-m", "--module", "modules", multiple=True, help="Process only specific module(s).")
@click.option("--since", default="", help="ISO date to start analysis from.")
@click.option("--dry-run", is_flag=True, help="Preview without writing to specification.")
@click.option("--debug", "debug_mode", is_flag=True, help="Debug mode: spawn all agents + finite loop (8 iterations, converges on idle).")
@click.option("--once", "once_mode", is_flag=True, help="With --debug: run a single iteration and exit.")
@click.option("--health", "show_health", is_flag=True, help="Show SRC-BUG-* status.")
@click.option("--watch", "watch_mode", is_flag=True, help="Run continuously every N seconds (all agents).")
@click.option("--interval", type=int, default=60, help="Seconds between checks in watch mode.")
def cycle_cmd(
    project_path: str | None,
    logs_path: str,
    modules: tuple[str, ...],
    since: str,
    dry_run: bool,
    debug_mode: bool,
    once_mode: bool,
    show_health: bool,
    watch_mode: bool,
    interval: int,
) -> None:
    """Ingest logs → analyst review → PM Agent (code gen + fix + deploy).

    Debug mode (--debug): runs a finite loop (max 8 iterations) with all
    agents spawned in background.  Converges after 2 idle iterations.
    Use --watch for continuous background-only operation.
    With --once: single pass through the full pipeline.

    Examples:
        spec-editor cycle                         # ingest logs only
        spec-editor cycle --debug                 # debug loop with all agents
        spec-editor cycle --debug --once           # single iteration with all agents
        spec-editor cycle --watch                 # continuous background agents
        spec-editor cycle --health                # show bug status
        spec-editor cycle --watch --interval 120  # continuous all agents
    """
    resolved = project_path or _get_project_path()

    # ── Clear stale .pyc before any agent imports ──
    _clear_pyc_cache(resolved)

    if show_health:
        _show_health(resolved)
        return

    if watch_mode:
        _acquire_lock(resolved)
        try:
            _run_watch(resolved, logs_path, list(modules), since, dry_run, interval)
        finally:
            _release_lock(resolved)
        return

    storage = _get_storage(resolved)
    console.print(f"[bold]Cycle[/bold] — {resolved}")
    console.print(f"  Logs: {logs_path}")
    if modules:
        console.print(f"  Modules: {', '.join(modules)}")

    # Always ensure opencode.json exists (project scaffolding).
    # Required for both --debug and ingest-only modes since the
    # coding team uses OpenCode under the hood.
    _ensure_opencode_config(Path(resolved), _get_cycle_logger(resolved))

    if debug_mode:
        mode_desc = "single pass" if once_mode else "loop until convergence"
        console.print(f"  Mode: [bold]full[/bold] ({mode_desc})")
        _acquire_lock(resolved)
        try:
            result = asyncio.run(_run_full_loop(resolved, single_pass=once_mode))
            console.print(f"\n[bold]Cycle complete[/bold]: {result['iterations']} iteration(s)")
        except ImportError as exc:
            console.print(f"[yellow]PM Agent workflow not available: {exc}[/yellow]")
        except Exception as exc:
            console.print(f"[red]Cycle failed: {exc}[/red]")
            import traceback
            traceback.print_exc()
        finally:
            _release_lock(resolved)
        return

    # Ingest-only mode (default, no --debug/--watch)
    result = asyncio.run(
        _ingest_and_push(storage, resolved, logs_path, list(modules) if modules else None, since, dry_run)
    )

    if result.get("errors"):
        for err in result["errors"]:
            console.print(f"[red]{err}[/red]")

    table = Table(title="Ingest Result")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Logs collected", str(result.get("logs_collected", 0)))
    table.add_row("Bugs found", str(result.get("bugs_found", 0)))
    table.add_row("SRC created", str(result.get("src_created", [])))
    console.print(table)

    if result.get("bugs_found", 0) > 0:
        console.print("\n[yellow]Bugs created as DRAFT. Run --debug or --watch to start agents.[/yellow]")


def _show_health(project_path: str) -> None:
    """Show SRC-BUG-* status."""
    storage = _get_storage(project_path)
    all_elements = storage.list_all()
    bugs = [s for s in all_elements if s.id.startswith("SRC-BUG-")]
    draft = sum(1 for s in bugs if s.status.value == "draft")
    reviewed = sum(1 for s in bugs if s.status.value == "reviewed")
    deprecated = sum(1 for s in bugs if s.status.value == "deprecated")

    console.print(f"[bold]Cycle Health[/bold] — {project_path}")
    table = Table(title="SRC-BUG-* Elements")
    table.add_column("Status", style="cyan")
    table.add_column("Count", style="green")
    table.add_row("Draft", str(draft))
    table.add_row("Reviewed", str(reviewed))
    table.add_row("Deprecated", str(deprecated))
    table.add_row("Total", str(len(bugs)))
    console.print(table)

    if draft == 0 and reviewed == 0:
        console.print("[green]No active bugs. System healthy.[/green]")


def _run_watch(project_path, logs_path, modules, since, dry_run, interval) -> None:
    """Continuous watch mode — all agents run in background via Redis."""
    import asyncio as _asyncio

    storage = _get_storage(project_path)

    # ── Recovery: reset stale dispatched tags before agents start ──
    _recover_stale_dispatched(storage, project_path)

    console.print(f"[bold]Cycle — Watch[/bold] (ingest every {interval}s, all agents active)")

    async def _watch_loop():
        from src.agents.persistent_agent import AgentWorker
        from pathlib import Path as _Path

        # ── Spawn all background agents ──
        from src.agents.constants import ALL_ROLES
        for role in ALL_ROLES:
            worker = AgentWorker(role=role, project_path=_Path(project_path))
            _asyncio.create_task(worker.run())
            console.print(f"[dim]  Spawned {role}[/dim]")

        console.print(
            "\n[bold green]All background agent queues active.[/bold green]\n"
            "[dim]To stop — press Ctrl+C or use [bold]spec-editor shutdown[/bold][/dim]\n"
        )

        while True:
            try:
                result = await _ingest_and_push(
                    storage, project_path, logs_path, modules, since, dry_run
                )
                if result.get("bugs_found", 0) > 0:
                    console.print(f"[yellow]{result['bugs_found']} bug(s) → PM Agent[/yellow]")
            except Exception as exc:
                console.print(f"[red]{exc}[/red]")
            await _asyncio.sleep(interval)

    try:
        _asyncio.run(_watch_loop())
    except KeyboardInterrupt:
        pass
    console.print("[dim]Watch mode stopped.[/dim]")


# ======================================================================
# logs — view agent activity logs
# ======================================================================


@click.command("logs")
@click.option("-p", "--project", "project_path", default=None)
@click.option(
    "-a",
    "--agent",
    "agent_filter",
    default=None,
    help="Filter by agent module (e.g. MOD-pm-agent).",
)
@click.option(
    "-n", "--lines", type=int, default=20, help="Number of recent lines to show."
)
@click.option(
    "-f", "--follow", is_flag=True, help="Tail -f mode: follow new log lines."
)
def logs_cmd(project_path=None, agent_filter=None, lines=20, follow=False):
    """View structured agent logs from the cycle."""
    import json

    resolved = project_path or _get_project_path()
    log_dir = Path(resolved) / "logs"

    if not log_dir.is_dir():
        console.print("[yellow]No logs/ directory found.[/yellow]")
        return

    if follow:
        _tail_logs(log_dir, agent_filter)
        return

    for agent_dir in sorted(log_dir.iterdir()):
        if not agent_dir.is_dir():
            continue
        if agent_filter and agent_dir.name != agent_filter:
            continue

        logfile = agent_dir / "structured.jsonl"
        if not logfile.is_file():
            continue

        all_lines = logfile.read_text().strip().split("\n")
        recent = all_lines[-lines:]

        sev_style = {"error": "red", "warning": "yellow", "info": "dim"}
        console.print(f"\n[bold]{agent_dir.name}[/bold] ({len(all_lines)} events)")

        for line in recent:
            try:
                e = json.loads(line.strip())
                ts = e["ts"][:19].replace("T", " ")
                sev = e["severity"]
                evt = e["event"]
                s = sev_style.get(sev, "")
                extra = {
                    k: v
                    for k, v in e.items()
                    if k
                    not in (
                        "module_id",
                        "scenario_id",
                        "element_id",
                        "event",
                        "severity",
                        "ts",
                    )
                }
                extra_str = " ".join(f"{k}={v}" for k, v in extra.items())
                console.print(
                    f"  [{s}]{ts}[/{s}]  [{s}]{sev:7s}[/{s}]  {evt:30s}  [dim]{extra_str}[/dim]"
                )
            except Exception:
                pass


def _tail_logs(log_dir, agent_filter):
    import json
    import signal
    import time

    # First, show all existing entries.
    console.print("[bold]Agent logs:[/bold]")
    for agent_dir in sorted(log_dir.iterdir()):
        if not agent_dir.is_dir():
            continue
        if agent_filter and agent_dir.name != agent_filter:
            continue
        logfile = agent_dir / "structured.jsonl"
        if not logfile.is_file():
            continue

        data = logfile.read_text().strip()
        if not data:
            continue

        console.print(f"\n[bold]{agent_dir.name}[/bold]")
        for line in data.split("\n"):
            if not line.strip():
                continue
            try:
                e = json.loads(line.strip())
                ts = e["ts"][:19].replace("T", " ")
                sev = e["severity"]
                evt = e["event"]
                extra = {
                    k: v
                    for k, v in e.items()
                    if k
                    not in (
                        "module_id",
                        "scenario_id",
                        "element_id",
                        "event",
                        "severity",
                        "ts",
                    )
                }
                extra_str = (
                    " ".join(f"{k}={v}" for k, v in extra.items()) if extra else ""
                )
                console.print(
                    f"  [dim]{ts}[/dim]  {sev:7s}  {evt:30s}  [dim]{extra_str}[/dim]"
                )
            except Exception:
                pass

    # Now record positions and watch for new entries.
    file_positions = {}
    for agent_dir in sorted(log_dir.iterdir()):
        if not agent_dir.is_dir():
            continue
        if agent_filter and agent_dir.name != agent_filter:
            continue
        logfile = agent_dir / "structured.jsonl"
        if logfile.is_file():
            file_positions[logfile] = logfile.stat().st_size

    if not file_positions:
        console.print("\n[yellow]No log files found.[/yellow]")
        return

    console.print(f"\n[bold]Watching for new events...[/bold] (Ctrl+C to stop)")
    running = True

    def _stop(signum, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while running:
        for logfile in list(file_positions.keys()):
            try:
                current_size = logfile.stat().st_size
                last_pos = file_positions[logfile]

                if current_size > last_pos:
                    with open(logfile, "r") as f:
                        f.seek(last_pos)
                        new_data = f.read()
                    file_positions[logfile] = current_size

                    for line in new_data.strip().split("\n"):
                        if not line.strip():
                            continue
                        try:
                            e = json.loads(line.strip())
                            agent = logfile.parent.name
                            ts = e["ts"][:19].replace("T", " ")
                            sev = e["severity"]
                            evt = e["event"]
                            extra = {
                                k: v
                                for k, v in e.items()
                                if k
                                not in (
                                    "module_id",
                                    "scenario_id",
                                    "element_id",
                                    "event",
                                    "severity",
                                    "ts",
                                )
                            }
                            extra_str = (
                                " ".join(f"{k}={v}" for k, v in extra.items())
                                if extra
                                else ""
                            )
                            console.print(
                                f"[dim]{ts}[/dim]  [bold]{agent}[/bold]  {sev:7s}  {evt:30s}  [dim]{extra_str}[/dim]"
                            )
                        except Exception:
                            pass
            except OSError:
                pass

        if not running:
            break
        time.sleep(1)

    console.print("\n[dim]Stopped.[/dim]")

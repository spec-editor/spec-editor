"""MCP tools for the cycle.

Provides handler functions for the four cycle phases.
Each tool is a callable that can be registered in the MCP handler.

Tools:
    run_log_analysis      — Phase 2: analyse logs, find bugs
    ingest_bugs           — Phase 3: convert bug reports to SRC-BUG-* elements
    update_spec_from_bugs — Phase 4: update specification from bugs
    run_cycle     — Phase 5: run the full cycle
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from spec_editor_cycle.analyzer import LogAnalyzer
from src.storage.adapter import StorageAdapter
from src.storage.models import Element, ElementStatus, Provenance

# Structured logging for agent observability.
_pm_log = None
_code_log = None


def _get_pm_log(log_dir: str = "logs"):
    global _pm_log
    if _pm_log is None:
        from src.tracing import StructuredLogEmitter

        _pm_log = StructuredLogEmitter(
            module_id="MOD-pm-agent",
            scenario_id="SCN-cycle",
            log_dir=log_dir,
            auto_element=False,
        )
    return _pm_log


def _get_code_log(log_dir: str = "logs"):
    global _code_log
    if _code_log is None:
        from src.tracing import StructuredLogEmitter

        _code_log = StructuredLogEmitter(
            module_id="MOD-coding-agent",
            scenario_id="SCN-code-fix",
            log_dir=log_dir,
            auto_element=False,
        )
    return _code_log


# ======================================================================
# Tool: run_log_analysis (Phase 2)
# ======================================================================


async def run_log_analysis_tool(
    storage: StorageAdapter,
    project_path: str,
    since: str = "",
    module_id: str = "",
) -> dict:
    """Analyse structured production logs and generate bug reports.

    Args:
        project_path: Path to the spec-editor project.
        since: ISO date string (e.g. ``"2025-06-20"``).
        module_id: Optional module filter.

    Returns summary with bugs found and their details.
    """
    analyzer = LogAnalyzer(project_path=project_path)
    bugs = analyzer.analyze(
        since=since,
        module_id=module_id if module_id else None,
    )

    bug_files: list[str] = []
    for bug in bugs:
        path = analyzer.save_bug_report(bug)
        bug_files.append(path.name)

    return {
        "status": "complete",
        "period": {"since": since, "until": "now"},
        "total_errors_analysed": sum(b.count for b in bugs),
        "bugs_found": len(bugs),
        "bugs": [
            {
                "title": b.title,
                "severity": b.severity,
                "module_id": b.module_id,
                "count": b.count,
                "is_new_pattern": b.is_new_pattern,
            }
            for b in bugs
        ],
        "bug_files": bug_files,
    }


# ======================================================================
# Tool: ingest_bugs (Phase 3)
# ======================================================================


async def ingest_bugs_tool(
    storage: StorageAdapter,
    project_path: str,
    dry_run: bool = False,
) -> dict:
    """Convert bug reports from sources_raw/ into SRC-BUG-* elements.

    Args:
        project_path: Path to the spec-editor project.
        dry_run: If True, preview without writing.

    Returns summary of created SRC elements.
    """
    sources_dir = Path(project_path) / "sources_raw"
    if not sources_dir.is_dir():
        return {"status": "error", "message": "sources_raw/ not found"}

    src_created: list[str] = []
    src_skipped: int = 0
    bugs_processed: int = 0

    for bug_file in sorted(sources_dir.glob("bugs_*.md")):
        # Skip already processed.
        content = bug_file.read_text(encoding="utf-8")
        if content.startswith("processed: true"):
            continue

        bugs_processed += 1

        # Parse bug report.
        title, description, module_id, element_ids, severity = _parse_bug_md(content)

        if not title:
            continue

        # Check for duplicates.
        if _bug_exists(storage, title, module_id):
            src_skipped += 1
            if not dry_run:
                _mark_processed(bug_file)
            continue

        # Create sequential SRC-BUG ID.
        bug_index = _next_bug_index(storage)
        src_id = f"SRC-BUG-{bug_index:03d}"

        if dry_run:
            src_created.append(src_id)
            continue

        # Build element — use full markdown content so fix_bugs has file paths.
        element = Element(
            aspect="sources",
            element_type="source",
            id=src_id,
            title=title,
            status=ElementStatus.DRAFT,
            content=content,
            derived_from=element_ids,
            provenance=Provenance(source="production_logs", confidence=0.95),
            tags=["bug", "production", severity],
        )

        storage.write_element(element)
        src_created.append(src_id)

        # Mark bug file as processed.
        _mark_processed(bug_file)

    return {
        "status": "complete",
        "bugs_processed": bugs_processed,
        "src_created": src_created,
        "src_skipped_duplicates": src_skipped,
        "dry_run": dry_run,
    }


# ======================================================================
# Tool: update_spec_from_bugs (Phase 4)
# ======================================================================


async def update_spec_from_bugs_tool(
    storage: StorageAdapter,
    bug_id: str = "",
    dry_run: bool = False,
) -> dict:
    """Update specification elements based on SRC-BUG-* requirements.

    Args:
        bug_id: Specific bug to process, or empty for all draft bugs.
        dry_run: If True, preview without writing.

    Returns summary of specification changes.
    """
    # Find draft SRC-BUG-* elements.
    bug_ids = [bug_id] if bug_id else _find_draft_bugs(storage)
    if not bug_ids:
        return {"status": "complete", "message": "No draft bugs to process"}

    elements_created: list[str] = []
    elements_updated: list[str] = []
    status_changes: list[dict] = []
    bugs_processed = 0

    for bid in bug_ids:
        try:
            bug = storage.read_element(bid)
        except KeyError:
            continue

        if bug.status != ElementStatus.DRAFT:
            continue

        bugs_processed += 1

        # Analyse the bug: what kind of spec change is needed?
        change = _analyse_bug_for_spec_change(bug)

        if change is None:
            # No spec change needed — mark as reviewed anyway.
            if not dry_run:
                bug.status = ElementStatus.REVIEWED
                storage.write_element(bug)
                status_changes.append({"id": bid, "from": "draft", "to": "reviewed"})
            continue

        if dry_run:
            elements_created.append(f"would-create: {change}")
            status_changes.append({"id": bid, "from": "draft", "to": "reviewed"})
            continue

        # Apply the change.
        result = _apply_spec_change(storage, bug, change)
        if result.get("created"):
            elements_created.extend(result["created"])
        if result.get("updated"):
            elements_updated.extend(result["updated"])

        # Mark bug as reviewed.
        bug.status = ElementStatus.REVIEWED
        storage.write_element(bug)
        status_changes.append({"id": bid, "from": "draft", "to": "reviewed"})

    return {
        "status": "complete",
        "bugs_processed": bugs_processed,
        "elements_created": elements_created,
        "elements_updated": elements_updated,
        "status_changes": status_changes,
        "dry_run": dry_run,
    }


# ======================================================================
# Tool: run_cycle (Phase 5)
# ======================================================================


async def run_cycle_tool(
    storage: StorageAdapter,
    project_path: str,
    logs_path: str = "logs/",
    modules: list[str] | None = None,
    since: str = "",
    analyze_only: bool = False,
    dry_run: bool = False,
) -> dict:
    """Run the complete cycle: collect → analyse → ingest → update.

    Args:
        project_path: Path to the spec-editor project.
        logs_path: Path to the application logs directory.
        modules: Optional list of module IDs to process.
        since: ISO date for log analysis.
        analyze_only: If True, stop after analysis phase.
        dry_run: If True, preview without writing.

    Returns comprehensive summary with per-phase results.
    """
    from spec_editor_cycle.collector import LogCollector

    phases_completed: list[str] = []
    errors: list[str] = []

    # Resolve logs_path relative to project_path if not absolute.
    logs_dir = Path(logs_path)
    if not logs_dir.is_absolute():
        logs_dir = Path(project_path) / logs_dir
    logs_path_resolved = str(logs_dir)
    log_dir = str(Path(project_path) / "logs")

    pm = _get_pm_log(log_dir=log_dir)
    pm.info("cycle_started", logs_path=logs_path_resolved)

    # Phase 1: Collect
    try:
        target_dir = str(Path(project_path) / "sources_raw")
        collector = LogCollector(source_dir=logs_path_resolved, target_dir=target_dir)
        collect_result = collector.sync()
        phases_completed.append("collect")
        pm.info("phase_collect_done", collected=collect_result["collected"])
    except Exception as exc:
        pm.error("phase_collect_failed", error=str(exc))
        return {"status": "error", "phase": "collect", "error": str(exc)}

    # Phase 2: Analyse
    try:
        analysis_result = await run_log_analysis_tool(
            storage=storage,
            project_path=project_path,
            since=since,
            module_id=modules[0] if modules and len(modules) == 1 else "",
        )
        phases_completed.append("analyze")
        pm.info("phase_analyze_done", bugs_found=analysis_result.get("bugs_found", 0))
    except Exception as exc:
        errors.append(f"analyze: {exc}")
        pm.warning("phase_analyze_failed", error=str(exc))
        analysis_result = {"bugs_found": 0, "bugs": [], "bug_files": []}

    if analyze_only:
        return {
            "status": "complete",
            "phases_completed": phases_completed,
            "logs_collected": collect_result.get("collected", 0),
            "bugs_found": analysis_result.get("bugs_found", 0),
            "errors": errors,
        }

    bugs_found = analysis_result.get("bugs_found", 0)
    if bugs_found == 0:
        return {
            "status": "complete",
            "phases_completed": phases_completed,
            "logs_collected": collect_result.get("collected", 0),
            "bugs_found": 0,
            "message": "No bugs found — skipping ingest and update phases",
        }

    # Phase 3: Ingest
    try:
        ingest_result = await ingest_bugs_tool(
            storage=storage,
            project_path=project_path,
            dry_run=dry_run,
        )
        phases_completed.append("ingest")
        pm.info("phase_ingest_done", src_created=ingest_result.get("src_created", []))
    except Exception as exc:
        errors.append(f"ingest: {exc}")
        pm.warning("phase_ingest_failed", error=str(exc))
        ingest_result = {"src_created": [], "src_skipped_duplicates": 0}

    # Phase 4: Update (only if new bugs were created)
    if not ingest_result.get("src_created"):
        return {
            "status": "complete",
            "phases_completed": phases_completed,
            "logs_collected": collect_result.get("collected", 0),
            "bugs_found": bugs_found,
            "src_created": [],
            "message": "No new bugs to ingest — skipping update phase",
            "errors": errors,
        }

    try:
        update_result = await update_spec_from_bugs_tool(
            storage=storage,
            dry_run=dry_run,
        )
        phases_completed.append("update")
        pm.info(
            "phase_update_done", spec_created=update_result.get("elements_created", [])
        )
    except Exception as exc:
        errors.append(f"update: {exc}")
        pm.warning("phase_update_failed", error=str(exc))
        update_result = {"elements_created": [], "elements_updated": []}

    spec_created = update_result.get("elements_created", [])
    pm.info(
        "cycle_complete",
        phases=phases_completed,
        bugs_found=bugs_found,
        spec_created=spec_created,
    )

    # If spec changes were made, log coding agent task
    if spec_created:
        code = _get_code_log(log_dir=log_dir)
        code.info("task_received", task=f"Implement {spec_created}", source="pm_agent")

    return {
        "status": "complete",
        "phases_completed": phases_completed,
        "logs_collected": collect_result.get("collected", 0),
        "bugs_found": bugs_found,
        "src_created": ingest_result.get("src_created", []),
        "src_skipped_duplicates": ingest_result.get("src_skipped_duplicates", 0),
        "spec_created": spec_created,
        "spec_updated": update_result.get("elements_updated", []),
        "bugs_moved_to_reviewed": update_result.get("status_changes", []),
        "errors": errors,
    }


# ======================================================================
# Handler builder for registration in the MCP server
# ======================================================================


def build_cycle_handlers(
    storage: StorageAdapter,
    project_path: str,
) -> dict[str, Callable]:
    """Build a dict of cycle tool handlers for MCP registration."""
    return {
        "run_log_analysis": lambda **kw: run_log_analysis_tool(
            storage, project_path=project_path, **kw
        ),
        "ingest_bugs": lambda **kw: ingest_bugs_tool(
            storage, project_path=project_path, **kw
        ),
        "update_spec_from_bugs": lambda **kw: update_spec_from_bugs_tool(storage, **kw),
        "run_cycle": lambda **kw: run_cycle_tool(
            storage, project_path=project_path, **kw
        ),
    }


# ======================================================================
# Helpers
# ======================================================================


def _parse_bug_md(content: str) -> tuple[str, str, str, list[str], str]:
    """Parse a bug report markdown file into its components."""
    lines = content.split("\n")
    title = ""
    module_id = ""
    element_ids: list[str] = []
    severity = "medium"

    for i, line in enumerate(lines):
        stripped = line.strip()

        if stripped.startswith("# Bug: "):
            title = stripped.replace("# Bug: ", "", 1)
        elif stripped.startswith("**Module:** "):
            module_id = stripped.replace("**Module:** ", "", 1)
        elif stripped.startswith("**Elements:** "):
            raw = stripped.replace("**Elements:** ", "", 1)
            element_ids = [e.strip() for e in raw.split(",") if e.strip()]
        elif stripped.startswith("**Severity:** "):
            severity = stripped.replace("**Severity:** ", "", 1)

    # Description: everything after ## Description heading.
    description = ""
    desc_start = -1
    for i, line in enumerate(lines):
        if line.strip() == "## Description":
            desc_start = i + 1
            break
    if desc_start > 0:
        desc_lines = []
        for line in lines[desc_start:]:
            if line.strip().startswith("## "):
                break
            desc_lines.append(line)
        description = "\n".join(desc_lines).strip()

    if not description:
        description = content

    return title, description, module_id, element_ids, severity


def _bug_exists(storage: StorageAdapter, title: str, module_id: str) -> bool:
    """Check if a bug with the same title and module already exists in the spec."""
    for summary in storage.list_all():
        if not summary.id.startswith("SRC-BUG-"):
            continue
        if summary.title == title:
            return True
    return False


def _next_bug_index(storage: StorageAdapter) -> int:
    """Find the next available SRC-BUG index."""
    max_idx = 0
    for summary in storage.list_all():
        if summary.id.startswith("SRC-BUG-"):
            try:
                idx = int(summary.id.split("-")[-1])
                max_idx = max(max_idx, idx)
            except ValueError:
                pass
    return max_idx + 1


def _mark_processed(filepath: Path) -> None:
    """Mark a bug report file as processed."""
    content = filepath.read_text(encoding="utf-8")
    if not content.startswith("processed: true"):
        filepath.write_text("processed: true\n" + content, encoding="utf-8")


def _find_draft_bugs(storage: StorageAdapter) -> list[str]:
    """Find all SRC-BUG-* elements with status=draft."""
    ids: list[str] = []
    for summary in storage.list_all():
        if not summary.id.startswith("SRC-BUG-"):
            continue
        if summary.status == ElementStatus.DRAFT:
            ids.append(summary.id)
    return ids


def _analyse_bug_for_spec_change(bug: Element) -> str | None:
    """Determine what kind of specification change is needed for a bug.

    Returns a change type string, or None if no change is needed.
    """
    content_lower = bug.content.lower()
    title_lower = bug.title.lower()

    # Error patterns → NFR.
    error_keywords = [
        "keyerror",
        "valueerror",
        "typeerror",
        "attributeerror",
        "validation",
        "null",
        "none",
        "missing",
    ]
    if any(kw in content_lower or kw in title_lower for kw in error_keywords):
        return "input_validation"

    # Timeout / slow → performance NFR.
    timeout_keywords = ["timeout", "slow", "deadline", "expired", "latency"]
    if any(kw in content_lower for kw in timeout_keywords):
        return "performance"

    # Connection / unavailable → health check component.
    conn_keywords = ["connection", "unavailable", "refused", "lost", "disconnect"]
    if any(kw in content_lower for kw in conn_keywords):
        return "health_check"

    # Race condition / concurrent → concurrency NFR.
    race_keywords = ["race", "concurrent", "deadlock", "lock", "atomic"]
    if any(kw in content_lower for kw in race_keywords):
        return "concurrency"

    # Data / corruption → entity field.
    data_keywords = ["corrupt", "data", "field", "column", "schema"]
    if any(kw in content_lower for kw in data_keywords):
        return "data_fix"

    # Security.
    sec_keywords = ["injection", "xss", "csrf", "auth", "token", "password", "leak"]
    if any(kw in content_lower for kw in sec_keywords):
        return "security"

    # Generic: assume an error handling step is needed.
    return "error_handling"


def _apply_spec_change(
    storage: StorageAdapter,
    bug: Element,
    change_type: str,
) -> dict:
    """Apply a specification change based on the bug.

    Returns dict with ``created`` and ``updated`` lists.
    """
    created: list[str] = []
    updated: list[str] = []

    nfr_index = _next_nfr_index(storage)
    bug_refs = bug.derived_from

    if change_type == "input_validation":
        nfr = Element(
            aspect="non_functional",
            element_type="requirement",
            id=f"NFR-{nfr_index:03d}",
            title=f"Input validation for {bug.module_id}"
            if hasattr(bug, "module_id")
            else f"Input validation — {bug.title[:60]}",
            content=f"All inputs to affected components must be validated before use.\n\nDerived from: {bug.id}",
            status=ElementStatus.DRAFT,
            derived_from=[bug.id],
            tags=["security", "reliability"],
        )
        storage.write_element(nfr)
        created.append(nfr.id)

    elif change_type == "performance":
        nfr = Element(
            aspect="non_functional",
            element_type="requirement",
            id=f"NFR-{nfr_index:03d}",
            title=f"Performance: address {bug.title[:60]}",
            content=f"Operations affected by this bug must meet performance targets.\n\nDerived from: {bug.id}",
            status=ElementStatus.DRAFT,
            derived_from=[bug.id],
            tags=["performance"],
        )
        storage.write_element(nfr)
        created.append(nfr.id)

    elif change_type in ("health_check", "concurrency", "security"):
        nfr = Element(
            aspect="non_functional",
            element_type="requirement",
            id=f"NFR-{nfr_index:03d}",
            title=f"{change_type.replace('_', ' ').title()}: {bug.title[:60]}",
            content=f"Requirement derived from production bug.\n\nDerived from: {bug.id}",
            status=ElementStatus.DRAFT,
            derived_from=[bug.id],
            tags=[change_type],
        )
        storage.write_element(nfr)
        created.append(nfr.id)

    elif change_type == "error_handling":
        # Create an error handling step for the affected scenario.
        for ref in bug_refs:
            if ref.startswith("SCN-") or ref.startswith("DSC-"):
                try:
                    target = storage.read_element(ref)
                    step_id = f"STP-{_next_step_index(storage):03d}"
                    step = Element(
                        aspect="user_scenarios",
                        element_type="step",
                        id=step_id,
                        title=f"Error handling for {bug.title[:50]}",
                        content=f"**Action:** System encounters {bug.title}\n**Expected Result:** System handles the error gracefully.\n\nDerived from: {bug.id}",
                        status=ElementStatus.DRAFT,
                        parent=ref,
                        derived_from=[bug.id],
                    )
                    storage.write_element(step)
                    created.append(step_id)
                    # Update parent's children.
                    if step_id not in target.children:
                        target.children = sorted(set(target.children) | {step_id})
                        target.derived_from = sorted(
                            set(target.derived_from) | {bug.id}
                        )
                        storage.write_element(target)
                        updated.append(ref)
                except KeyError:
                    pass

    else:
        # Default: just update affected elements with bug reference.
        for ref in bug_refs:
            try:
                target = storage.read_element(ref)
                if bug.id not in target.derived_from:
                    target.derived_from = sorted(set(target.derived_from) | {bug.id})
                    storage.write_element(target)
                    updated.append(ref)
            except KeyError:
                pass

    return {"created": created, "updated": updated}


def _next_nfr_index(storage: StorageAdapter) -> int:
    """Find the next available NFR index."""
    max_idx = 0
    for summary in storage.list_all():
        if summary.id.startswith("NFR-"):
            try:
                idx = int(summary.id.split("-")[-1])
                max_idx = max(max_idx, idx)
            except ValueError:
                pass
    return max_idx + 1


def _next_step_index(storage: StorageAdapter) -> int:
    """Find the next available STP index."""
    max_idx = 0
    for summary in storage.list_all():
        if summary.id.startswith("STP-"):
            try:
                parts = summary.id.split("-")
                idx = int(parts[-1])
                max_idx = max(max_idx, idx)
            except (ValueError, IndexError):
                pass
    return max_idx + 1

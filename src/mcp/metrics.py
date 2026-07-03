"""MCP module: connectivity metrics calculation and snapshots."""

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from src.config import get_logger
from src.storage.adapter import StorageAdapter
from src.storage.models import ElementStatus, ElementSummary
from src.storage.queries import load_all_elements

logger = get_logger(__name__)


# ------------------------------------------------------------------
# Metrics
# ------------------------------------------------------------------


class MetricsReport(BaseModel):
    """Report with specification metrics."""

    total_elements: int = 0
    total_relationships: int = 0
    cross_aspect_relationships: int = 0
    connectivity_index: float = 0.0
    orphan_elements: int = 0
    unparented_elements: int = 0
    unparented_by_aspect: dict[str, int] = Field(default_factory=dict)
    coverage_ratio: float = 0.0
    aspects: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)

    # Cycle metrics.
    bugs_total: int = 0
    bugs_open: int = 0
    bugs_reviewed: int = 0
    bugs_confirmed: int = 0
    bugs_resolved: int = 0
    bugs_critical: int = 0
    bugs_high: int = 0
    bugs_medium: int = 0
    bugs_low: int = 0
    cycles_completed: int = 0
    cycles_failed: int = 0
    last_loop_duration_seconds: float = 0.0
    last_loop_ts: str = ""
    requirements_with_bugs: int = 0
    modules_with_errors: int = 0


def compute_metrics(storage: StorageAdapter) -> MetricsReport:
    """Compute metrics for the current specification state."""

    all_summaries = storage.list_all()
    total_elements = len(all_summaries)

    if total_elements == 0:
        return MetricsReport()

    # Collect full elements
    elements = load_all_elements(storage)

    # Calculate metrics
    total_relationships = 0
    cross_aspect_relationships = 0
    aspects_count: dict[str, int] = {}
    by_status: dict[str, int] = {}

    for element in elements:
        # Aspects
        aspects_count[element.aspect] = aspects_count.get(element.aspect, 0) + 1

        # Statuses
        status = element.status.value
        by_status[status] = by_status.get(status, 0) + 1

        # Relationships: parent + children + relationships
        has_any_relationship = False

        if element.parent:
            total_relationships += 1
            has_any_relationship = True

        total_relationships += len(element.children)
        if element.children:
            has_any_relationship = True

        for rel_type, entries in element.relationships.items():
            total_relationships += len(entries)
            has_any_relationship = True
            # Cross-aspect: check target.aspect != element.aspect
            for entry in entries:
                try:
                    target = storage.read_element(entry.target)
                    if target.aspect != element.aspect:
                        cross_aspect_relationships += 1
                except Exception:
                    pass  # broken link — don't count

    # Connectivity index
    connectivity_index = (
        cross_aspect_relationships / total_elements if total_elements > 0 else 0.0
    )

    # Orphans — exclude SRC elements (they don't participate in structural relationships)
    orphan_elements = sum(
        1
        for e in elements
        if not e.id.startswith("SRC-")
        and not e.parent
        and not e.children
        and not e.relationships
    )

    # Unparented — elements without parent in hierarchical aspects.
    # Root types are derived from methodology: first element_type in each aspect.
    from src.config.methodology import get_root_types, load_methodology

    _ROOT_TYPES: set[str] = set()
    try:
        # Derive methodology path from storage's project directory
        aspects_dir = getattr(storage, '_aspects_path', Path.cwd() / "aspects")
        method_path = Path(aspects_dir).parent / "methodology.yaml"
        if method_path.exists():
            method = load_methodology(method_path)
            _ROOT_TYPES = get_root_types(method)
    except Exception:
        pass
    if not _ROOT_TYPES:
        _ROOT_TYPES = {"source"}
    unparented = 0
    unparented_by_aspect: dict[str, int] = {}
    for e in elements:
        if e.id.startswith("SRC-"):
            continue
        if e.element_type in _ROOT_TYPES:
            continue
        if e.parent:
            continue
        unparented += 1
        unparented_by_aspect[e.aspect] = unparented_by_aspect.get(e.aspect, 0) + 1

    # Coverage — count reviewed or confirmed as "covered"
    reviewed_count = by_status.get(ElementStatus.REVIEWED.value, 0)
    confirmed = by_status.get(ElementStatus.CONFIRMED.value, 0)
    coverage_ratio = (
        (reviewed_count + confirmed) / total_elements if total_elements > 0 else 0.0
    )

    # Cycle metrics.
    cycle = _compute_cycle_metrics(storage, elements)

    return MetricsReport(
        total_elements=total_elements,
        total_relationships=total_relationships,
        cross_aspect_relationships=cross_aspect_relationships,
        connectivity_index=round(connectivity_index, 4),
        orphan_elements=orphan_elements,
        unparented_elements=unparented,
        unparented_by_aspect=unparented_by_aspect,
        coverage_ratio=round(coverage_ratio, 4),
        aspects=aspects_count,
        by_status=by_status,
        **cycle,
    )


def compute_delta(before: MetricsReport, after: MetricsReport) -> dict:
    """Compute the delta between two metrics reports."""
    return {
        "total_elements": after.total_elements - before.total_elements,
        "total_relationships": after.total_relationships - before.total_relationships,
        "cross_aspect_relationships": after.cross_aspect_relationships
        - before.cross_aspect_relationships,
        "connectivity_index": round(
            after.connectivity_index - before.connectivity_index, 4
        ),
        "orphan_elements": after.orphan_elements - before.orphan_elements,
        "unparented_elements": after.unparented_elements - before.unparented_elements,
        "coverage_ratio": round(after.coverage_ratio - before.coverage_ratio, 4),
        "bugs_total": after.bugs_total - before.bugs_total,
        "bugs_resolved": after.bugs_resolved - before.bugs_resolved,
    }


def _compute_cycle_metrics(storage, elements) -> dict:
    """Compute cycle-specific metrics from specification elements."""
    bug_elements = [e for e in elements if e.id.startswith("SRC-BUG-")]

    bugs_by_status: dict[str, int] = {}
    bugs_by_severity: dict[str, int] = {}

    for bug in bug_elements:
        bugs_by_status[bug.status.value] = bugs_by_status.get(bug.status.value, 0) + 1
        for tag in bug.tags:
            if tag in ("critical", "high", "medium", "low"):
                bugs_by_severity[tag] = bugs_by_severity.get(tag, 0) + 1
                break

    # Count non-source elements that derive from any SRC-BUG-*.
    bug_ids = {b.id for b in bug_elements}
    requirements_with_bugs = sum(
        1
        for e in elements
        if not e.id.startswith("SRC-") and any(ref in bug_ids for ref in e.derived_from)
    )

    # Modules with active (draft/reviewed) bugs.
    modules_with_errors: set[str] = set()
    for bug in bug_elements:
        if bug.status.value in ("draft", "reviewed"):
            for ref in bug.derived_from:
                if ref.startswith("MOD-"):
                    modules_with_errors.add(ref)

    return {
        "bugs_total": len(bug_elements),
        "bugs_open": bugs_by_status.get("draft", 0),
        "bugs_reviewed": bugs_by_status.get("reviewed", 0),
        "bugs_confirmed": bugs_by_status.get("confirmed", 0),
        "bugs_resolved": bugs_by_status.get("deprecated", 0),
        "bugs_critical": bugs_by_severity.get("critical", 0),
        "bugs_high": bugs_by_severity.get("high", 0),
        "bugs_medium": bugs_by_severity.get("medium", 0),
        "bugs_low": bugs_by_severity.get("low", 0),
        "cycles_completed": 0,
        "cycles_failed": 0,
        "last_loop_duration_seconds": 0.0,
        "last_loop_ts": "",
        "requirements_with_bugs": requirements_with_bugs,
        "modules_with_errors": len(modules_with_errors),
    }


# ------------------------------------------------------------------
# Snapshot
# ------------------------------------------------------------------


class Snapshot(BaseModel):
    """Immutable snapshot of specification state."""

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Snapshot timestamp",
    )
    elements: dict[str, ElementSummary] = Field(
        default_factory=dict,
        description="All elements at snapshot time",
    )
    metrics: MetricsReport | None = Field(
        default=None,
        description="Metrics at snapshot time",
    )


def take_snapshot(
    storage: StorageAdapter,
    compute_metrics_flag: bool = True,
) -> Snapshot:
    """Take a snapshot of the current specification state."""

    elements_dict: dict[str, ElementSummary] = {}
    for summary in storage.list_all():
        elements_dict[summary.id] = summary

    metrics = compute_metrics(storage) if compute_metrics_flag else None

    return Snapshot(
        elements=elements_dict,
        metrics=metrics,
    )

"""MCP module: connectivity metrics calculation and snapshots."""

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from src.config import get_logger
from src.storage.adapter import StorageAdapter
from src.storage.models import ElementStatus, ElementSummary

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
    coverage_ratio: float = 0.0
    aspects: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)


def compute_metrics(storage: StorageAdapter) -> MetricsReport:
    """Compute metrics for the current specification state."""

    all_summaries = storage.list_all()
    total_elements = len(all_summaries)

    if total_elements == 0:
        return MetricsReport()

    # Collect full elements
    elements: list = []
    for summary in all_summaries:
        try:
            elements.append(storage.read_element(summary.id))
        except Exception:
            logger.warning("skip_element_for_metrics", element_id=summary.id)

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

    # Orphans
    orphan_elements = sum(
        1 for e in elements if not e.parent and not e.children and not e.relationships
    )

    # Coverage
    confirmed = by_status.get(ElementStatus.CONFIRMED.value, 0)
    coverage_ratio = confirmed / total_elements if total_elements > 0 else 0.0

    return MetricsReport(
        total_elements=total_elements,
        total_relationships=total_relationships,
        cross_aspect_relationships=cross_aspect_relationships,
        connectivity_index=round(connectivity_index, 4),
        orphan_elements=orphan_elements,
        coverage_ratio=round(coverage_ratio, 4),
        aspects=aspects_count,
        by_status=by_status,
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
        "coverage_ratio": round(after.coverage_ratio - before.coverage_ratio, 4),
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

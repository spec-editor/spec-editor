"""Data models for the cycle: bug reports, loop state, baselines."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class BugReport:
    """A bug detected from production logs.

    Created by :class:`LogAnalyzer` and consumed by :class:`BugIngestor`
    which converts it into an SRC-BUG-* specification element.
    """

    title: str
    description: str
    module_id: str
    element_ids: list[str] = field(default_factory=list)
    scenario_ids: list[str] = field(default_factory=list)
    severity: str = "medium"  # critical / high / medium / low
    error_samples: list[dict[str, Any]] = field(default_factory=list)
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    count: int = 0
    is_new_pattern: bool = False

    _SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

    # -- computed ------------------------------------------------------------

    def severity_rank(self) -> int:
        """Integer rank for sorting (0 = most severe)."""
        return self._SEVERITY_ORDER.get(self.severity, 99)

    @property
    def error_rate_per_hour(self) -> float:
        """Errors per hour over the observed period."""
        if self.first_seen is None or self.last_seen is None:
            return 0.0
        hours = (self.last_seen - self.first_seen).total_seconds() / 3600.0
        if hours <= 0:
            return float(self.count)
        return self.count / hours

    # -- serialisation -------------------------------------------------------

    def to_markdown(self) -> str:
        """Render the bug report as a Markdown document for sources_raw/."""
        lines = [
            f"# Bug: {self.title}",
            "",
            f"**Module:** {self.module_id}",
        ]
        if self.element_ids:
            lines.append(f"**Elements:** {', '.join(self.element_ids)}")
        if self.scenario_ids:
            lines.append(f"**Scenarios:** {', '.join(self.scenario_ids)}")
        lines.extend(
            [
                f"**Severity:** {self.severity}",
                f"**New pattern:** {self.is_new_pattern}",
                "",
            ]
        )
        if self.first_seen and self.last_seen:
            lines.append(
                f"**Period:** {self.first_seen.isoformat()} – "
                f"{self.last_seen.isoformat()}"
            )
        lines.extend(
            [
                f"**Occurrences:** {self.count}",
                f"**Rate:** {self.error_rate_per_hour:.1f}/hour",
                "",
                "## Description",
                "",
                self.description,
                "",
            ]
        )
        if self.error_samples:
            lines.append("## Error Samples")
            lines.append("")
            for i, sample in enumerate(self.error_samples[:5], 1):
                lines.append(f"### Sample {i}")
                lines.append("```")
                for key, value in sample.items():
                    lines.append(f"{key}: {value}")
                lines.append("```")
                lines.append("")
        return "\n".join(lines)


@dataclass
class CycleLoopState:
    """Persistent state for the cycle coordinator."""

    last_run_ts: str = ""  # ISO timestamp
    last_log_position: dict[str, int] = field(default_factory=dict)
    bugs_found_total: int = 0
    bugs_resolved_total: int = 0
    loops_completed: int = 0
    loops_failed: int = 0


@dataclass
class BaselineEntry:
    """Historical baseline for a specific (module_id, event) pair."""

    module_id: str
    event: str
    daily_counts: dict[str, int] = field(default_factory=dict)
    avg_per_hour: float = 0.0
    updated_at: str = ""

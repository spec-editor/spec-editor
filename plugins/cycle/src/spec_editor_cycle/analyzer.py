"""Log Analyzer — detects bugs from structured JSON-lines production logs.

Reads logs from ``sources_raw/logs_MOD-*.jsonl``, groups errors, detects
spikes and new patterns, and generates :class:`BugReport` objects.

Usage::

    from spec_editor_cycle.analyzer import LogAnalyzer

    analyzer = LogAnalyzer(project_path="my-project")
    bugs = analyzer.analyze(since="2025-06-20")
    # → list[BugReport]

    for bug in bugs:
        analyzer.save_bug_report(bug)
        # → sources_raw/bugs_MOD-001_1718928000.md
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spec_editor_cycle.models import BaselineEntry, BugReport


class LogAnalyzer:
    """Detects bugs from structured production logs.

    Args:
        project_path: Path to the spec-editor project root
                      (must contain ``sources_raw/``).
    """

    SPIKE_MULTIPLIER = 3.0
    MIN_COUNT_FOR_SPIKE = 1
    MAX_ERROR_SAMPLES = 5

    def __init__(self, project_path: str | Path) -> None:
        self._project_path = Path(project_path)
        self._sources_dir = self._project_path / "sources_raw"
        self._baseline_dir = self._sources_dir / ".baselines"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        since: str = "",
        module_id: str | None = None,
    ) -> list[BugReport]:
        """Analyze logs and return a list of detected bugs.

        Args:
            since: ISO date string (e.g. ``"2025-06-20"``).
                   Only logs from this date onward are analysed.
            module_id: If set, restrict analysis to a single module.

        Returns:
            List of :class:`BugReport` objects for significant findings.
        """
        # 1. Read and filter log entries.
        entries = self._read_logs(since, module_id)
        if not entries:
            return []

        # 2. Group errors by (module_id, element_id, event).
        groups = self._group_errors(entries)

        # 3. For each group, compute stats and detect anomalies.
        bugs: list[BugReport] = []
        for (mod_id, elem_id, event), group_entries in groups.items():
            bug = self._analyse_group(mod_id, elem_id, event, group_entries)
            if bug is not None:
                bugs.append(bug)

        # 4. Update baselines.
        self._update_baselines(groups)

        return sorted(bugs, key=lambda b: (b.severity_rank(), -b.count))

    def save_bug_report(self, bug: BugReport) -> Path:
        """Save a bug report as a Markdown file in ``sources_raw/``.

        Returns the path to the created file.
        """
        ts = int(time.time())
        filename = f"bugs_{bug.module_id}_{ts}.md"
        filepath = self._sources_dir / filename
        filepath.write_text(bug.to_markdown(), encoding="utf-8")
        return filepath

    # ------------------------------------------------------------------
    # Log reading
    # ------------------------------------------------------------------

    def _read_logs(
        self,
        since: str,
        module_id: str | None,
    ) -> list[dict[str, Any]]:
        """Read JSON-lines log files and return error/warning entries."""
        entries: list[dict[str, Any]] = []
        since_ts = self._parse_since(since)

        pattern = f"logs_{module_id}_*.jsonl" if module_id else "logs_MOD-*.jsonl"

        for log_file in sorted(self._sources_dir.glob(pattern)):
            try:
                for line in log_file.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        entry = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue

                    # Filter by severity.
                    severity = entry.get("severity", "")
                    if severity not in ("error", "warning"):
                        continue

                    # Filter by date.
                    ts_str = entry.get("ts", "")
                    if since_ts and ts_str < since_ts:
                        continue

                    entries.append(entry)
            except OSError:
                continue

        return entries

    # ------------------------------------------------------------------
    # Grouping
    # ------------------------------------------------------------------

    @staticmethod
    def _group_errors(
        entries: list[dict[str, Any]],
    ) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
        """Group entries by (module_id, element_id, event)."""
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for entry in entries:
            mod_id = entry.get("module_id", "unknown")
            elem_id = entry.get("element_id", "")
            event = entry.get("event", "unknown")
            groups[(mod_id, elem_id, event)].append(entry)
        return dict(groups)

    # ------------------------------------------------------------------
    # Per-group analysis
    # ------------------------------------------------------------------

    def _analyse_group(
        self,
        mod_id: str,
        elem_id: str,
        event: str,
        entries: list[dict[str, Any]],
    ) -> BugReport | None:
        """Analyse a single error group and return a BugReport if significant."""
        count = len(entries)

        # Timestamps.
        timestamps = []
        for e in entries:
            ts_str = e.get("ts", "")
            try:
                ts = datetime.fromisoformat(ts_str)
                timestamps.append(ts)
            except (ValueError, TypeError):
                pass

        first_seen = min(timestamps) if timestamps else None
        last_seen = max(timestamps) if timestamps else None

        # Error samples.
        samples = entries[: self.MAX_ERROR_SAMPLES]

        # Scenario IDs from entries.
        scenario_ids: list[str] = []
        for e in entries:
            sid = e.get("scenario_id", "")
            if sid and sid not in scenario_ids:
                scenario_ids.append(sid)

        # Build element_ids list.
        element_ids = [elem_id] if elem_id else []
        # Add any additional element_ids found in entries.
        for e in entries:
            eid = e.get("element_id", "")
            if eid and eid not in element_ids:
                element_ids.append(eid)

        # Baseline comparison.
        baseline = self._load_baseline(mod_id, event)
        is_spike = self._is_spike(count, baseline, timestamps)
        is_new = self._is_new_pattern(mod_id, event)

        # Severity classification.
        severity = self._classify_severity(
            count, is_spike, timestamps, first_seen, last_seen
        )

        # Always report errors in dev cycle — any error is a bug.
        if count == 0:
            return None

        # Build description.
        description = self._build_description(
            mod_id,
            elem_id,
            event,
            count,
            first_seen,
            last_seen,
            is_spike,
            is_new,
            baseline,
        )

        return BugReport(
            title=f"{mod_id}: {event}",
            description=description,
            module_id=mod_id,
            element_ids=element_ids,
            scenario_ids=scenario_ids,
            severity=severity,
            error_samples=samples,
            first_seen=first_seen,
            last_seen=last_seen,
            count=count,
            is_new_pattern=is_new,
        )

    # ------------------------------------------------------------------
    # Spike detection
    # ------------------------------------------------------------------

    @staticmethod
    def _is_spike(
        count: int,
        baseline: BaselineEntry | None,
        timestamps: list[datetime],
    ) -> bool:
        """Check if the current error count is a spike vs. baseline."""
        if count < LogAnalyzer.MIN_COUNT_FOR_SPIKE:
            return False

        if baseline is None or baseline.avg_per_hour == 0.0:
            # No baseline — any errors above threshold are a spike.
            return count >= LogAnalyzer.MIN_COUNT_FOR_SPIKE

        # Compute current rate.
        if len(timestamps) < 2:
            hours = 24.0  # assume 24h if no range
        else:
            hours = (max(timestamps) - min(timestamps)).total_seconds() / 3600.0
            hours = max(hours, 0.1)  # avoid division by zero
        current_rate = count / hours

        return current_rate > LogAnalyzer.SPIKE_MULTIPLIER * baseline.avg_per_hour

    # ------------------------------------------------------------------
    # New pattern detection
    # ------------------------------------------------------------------

    def _is_new_pattern(self, module_id: str, event: str) -> bool:
        """Check if this event has been seen before."""
        baseline = self._load_baseline(module_id, event)
        return baseline is None

    # ------------------------------------------------------------------
    # Severity classification
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_severity(
        count: int,
        is_spike: bool,
        timestamps: list[datetime],
        first_seen: datetime | None,
        last_seen: datetime | None,
    ) -> str:
        """Classify bug severity based on count, spike, and time span."""
        # Compute error rate.
        if first_seen and last_seen and len(timestamps) >= 2:
            hours = (last_seen - first_seen).total_seconds() / 3600.0
            hours = max(hours, 0.1)
            rate = count / hours
        else:
            rate = float(count)

        if rate > 50:
            return "critical"
        if rate > 10 or (is_spike and count > 20):
            return "high"
        if rate > 1 or is_spike:
            return "medium"
        return "low"

    # ------------------------------------------------------------------
    # Description builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_description(
        mod_id: str,
        elem_id: str,
        event: str,
        count: int,
        first_seen: datetime | None,
        last_seen: datetime | None,
        is_spike: bool,
        is_new: bool,
        baseline: BaselineEntry | None,
    ) -> str:
        """Build a human-readable description for the bug report."""
        parts = [
            f"Error event `{event}` occurred **{count}** times",
        ]

        if elem_id:
            parts.append(f"in element `{elem_id}`")

        parts.append(f"within module `{mod_id}`.")

        if first_seen and last_seen:
            parts.append(
                f"\n\nPeriod: {first_seen.isoformat()} – {last_seen.isoformat()}"
            )

        if is_spike:
            baseline_rate = baseline.avg_per_hour if baseline else 0.0
            parts.append(f"\n\n**Spike detected!** ")
            if baseline_rate > 0:
                parts.append(f"Baseline rate: {baseline_rate:.1f}/hour.")
            else:
                parts.append("No prior baseline — this is a new problem.")

        if is_new:
            parts.append(
                "\n\n**New error pattern** — this event has not been seen before."
            )

        return "".join(parts)

    # ------------------------------------------------------------------
    # Baseline persistence
    # ------------------------------------------------------------------

    def _load_baseline(self, module_id: str, event: str) -> BaselineEntry | None:
        """Load a baseline entry from disk."""
        safe_event = event.replace("/", "_").replace(" ", "_")
        path = self._baseline_dir / f"{module_id}_{safe_event}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return BaselineEntry(
                module_id=data.get("module_id", module_id),
                event=data.get("event", event),
                daily_counts=data.get("daily_counts", {}),
                avg_per_hour=data.get("avg_per_hour", 0.0),
                updated_at=data.get("updated_at", ""),
            )
        except (json.JSONDecodeError, OSError):
            return None

    def _update_baselines(
        self,
        groups: dict[tuple[str, str, str], list[dict[str, Any]]],
    ) -> None:
        """Update baseline files with today's counts."""
        self._baseline_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        for (mod_id, _elem_id, event), entries in groups.items():
            count = len(entries)
            baseline = self._load_baseline(mod_id, event)

            if baseline is None:
                baseline = BaselineEntry(
                    module_id=mod_id,
                    event=event,
                    daily_counts={},
                )

            # Update daily count.
            baseline.daily_counts[today] = baseline.daily_counts.get(today, 0) + count

            # Recompute average.
            total = sum(baseline.daily_counts.values())
            days = len(baseline.daily_counts)
            baseline.avg_per_hour = round(total / max(days, 1) / 24.0, 4)
            baseline.updated_at = datetime.now(timezone.utc).isoformat()

            # Keep only last 30 days.
            sorted_days = sorted(baseline.daily_counts.keys(), reverse=True)
            baseline.daily_counts = {
                d: baseline.daily_counts[d] for d in sorted_days[:30]
            }

            self._save_baseline(baseline)

    def _save_baseline(self, baseline: BaselineEntry) -> None:
        """Save a baseline entry to disk."""
        safe_event = baseline.event.replace("/", "_").replace(" ", "_")
        path = self._baseline_dir / f"{baseline.module_id}_{safe_event}.json"
        path.write_text(
            json.dumps(
                {
                    "module_id": baseline.module_id,
                    "event": baseline.event,
                    "daily_counts": baseline.daily_counts,
                    "avg_per_hour": baseline.avg_per_hour,
                    "updated_at": baseline.updated_at,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_since(since: str) -> str:
        """Parse a ``since`` date string into an ISO timestamp prefix."""
        if not since:
            return ""
        # Accept "2025-06-20" or full ISO.
        if len(since) == 10 and since[4] == "-":
            return since + "T00:00:00"
        return since

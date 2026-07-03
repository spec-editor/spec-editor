"""PM Agent — proactive health checks (no LLM, pure code).

Each check returns a list of Issue objects.  The PM agent runs these
periodically and acts on the results without spending tokens.

Checks are registered via ``@scan_check`` decorator — the PM agent
can add new checks by editing this file or via skills config.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class Issue:
    """A problem found by a proactive scan check."""

    check_name: str
    severity: str  # "error", "warning", "info"
    element_id: str = ""
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


# ── Registry ──

_checks: list[Callable] = []


def scan_check(fn: Callable) -> Callable:
    """Decorator: register a proactive scan check."""
    _checks.append(fn)
    return fn


def get_checks() -> list[Callable]:
    return list(_checks)


# ═══════════════════════════════════════════════════════════════════════════
# Built-in checks
# ═══════════════════════════════════════════════════════════════════════════


@scan_check
def check_blocked_bugs_without_note(storage: Any, project_path: Path) -> list[Issue]:
    """Find BLOCKED bugs that don't have a PM refinement note."""
    issues: list[Issue] = []
    for summary in storage.list_all():
        if not summary.id.startswith("SRC-BUG-"):
            continue
        try:
            bug = storage.read_element(summary.id)
        except Exception:
            continue

        if bug.status.value != "blocked":
            continue

        leaf_id = bug.derived_from[0] if bug.derived_from else ""
        has_note = False
        if leaf_id:
            try:
                leaf = storage.read_element(leaf_id)
                has_note = "PM Refinement:" in (leaf.content or "")
            except Exception:
                pass

        if not has_note:
            issues.append(
                Issue(
                    check_name="blocked_without_note",
                    severity="error",
                    element_id=bug.id,
                    message=f"BLOCKED bug {bug.id} has no PM refinement note",
                    data={
                        "bug_id": bug.id,
                        "leaf_id": leaf_id,
                        "bug_title": bug.title,
                        "bug_content": bug.content or "",
                    },
                )
            )
    return issues


@scan_check
def check_dangling_references(storage: Any, project_path: Path) -> list[Issue]:
    """Find elements that reference non-existent elements."""
    issues: list[Issue] = []
    all_ids = {s.id for s in storage.list_all()}

    for summary in storage.list_all():
        try:
            el = storage.read_element(summary.id)
        except Exception:
            continue

        # Check derived_from
        for ref in el.derived_from or []:
            if ref and ref not in all_ids:
                issues.append(
                    Issue(
                        check_name="dangling_reference",
                        severity="warning",
                        element_id=el.id,
                        message=f"{el.id} references non-existent {ref} in derived_from",
                        data={"ref_id": ref, "field": "derived_from"},
                    )
                )

        # Check relationships
        for rel_type, entries in (el.relationships or {}).items():
            for entry in entries:
                if entry.target and entry.target not in all_ids:
                    issues.append(
                        Issue(
                            check_name="dangling_reference",
                            severity="warning",
                            element_id=el.id,
                            message=f"{el.id} relationship {rel_type} → {entry.target} (not found)",
                            data={"ref_id": entry.target, "field": "relationships"},
                        )
                    )

    return issues


@scan_check
def check_overreactivated_bugs(storage: Any, project_path: Path) -> list[Issue]:
    """Find bugs that have been reactivated too many times."""
    issues: list[Issue] = []
    for summary in storage.list_all():
        if not summary.id.startswith("SRC-BUG-"):
            continue
        try:
            bug = storage.read_element(summary.id)
        except Exception:
            continue

        # Count refined_by_pm occurrences
        refine_count = sum(1 for t in (bug.tags or []) if "refined_by_pm" in t)
        if refine_count >= 2:
            issues.append(
                Issue(
                    check_name="overreactivated",
                    severity="warning",
                    element_id=bug.id,
                    message=f"{bug.id} has been reactivated {refine_count} times — may need manual review",
                    data={"bug_id": bug.id, "reactivations": refine_count},
                )
            )
    return issues


@scan_check
def check_new_build_errors(storage: Any, project_path: Path) -> list[Issue]:
    """Check build logs for errors since last scan."""
    issues: list[Issue] = []
    log_file = project_path / "logs" / "MOD-build" / "structured.jsonl"
    if not log_file.is_file():
        return issues

    # Track last scan position
    state_file = project_path / ".pm-scan-state.json"
    last_pos = 0
    if state_file.exists():
        try:
            import json

            state = json.loads(state_file.read_text())
            last_pos = state.get("build_log_pos", 0)
        except Exception:
            pass

    try:
        lines = log_file.read_text().splitlines()
        new_errors = 0
        for i, line in enumerate(lines):
            if i < last_pos:
                continue
            if '"severity": "error"' in line or '"severity": "warning"' in line:
                new_errors += 1

        # Save position
        state_file.parent.mkdir(parents=True, exist_ok=True)
        import json

        state_file.write_text(json.dumps({"build_log_pos": len(lines)}))

        if new_errors > 0:
            issues.append(
                Issue(
                    check_name="build_errors",
                    severity="error" if new_errors > 5 else "warning",
                    element_id="",
                    message=f"Found {new_errors} new error/warning entries in build log",
                    data={"new_errors": new_errors},
                )
            )
    except Exception:
        pass

    return issues


# ═══════════════════════════════════════════════════════════════════════════
# Scanner
# ═══════════════════════════════════════════════════════════════════════════


async def run_proactive_scan(
    storage: Any,
    project_path: Path,
    logger: Any,
) -> dict[str, int]:
    """Run all registered checks.  Returns counts by severity.

    Called by PM agent's background loop every N seconds.
    """
    counts: dict[str, int] = {"error": 0, "warning": 0, "info": 0}
    total = 0

    for check_fn in _checks:
        try:
            issues = check_fn(storage, project_path)
            for issue in issues:
                counts[issue.severity] = counts.get(issue.severity, 0) + 1
                total += 1
                logger.info(
                    "pm_scan_issue",
                    check=issue.check_name,
                    issue_severity=issue.severity,
                    element_id=issue.element_id,
                    message=issue.message[:200],
                )
        except Exception as exc:
            logger.error(
                "pm_scan_check_failed", check=check_fn.__name__, error=str(exc)
            )

    if total > 0:
        logger.info(
            "pm_scan_summary",
            total=total,
            errors=counts.get("error", 0),
            warnings=counts.get("warning", 0),
        )

    return counts

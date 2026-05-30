"""INVEST validator for user stories.

Checks each user story against the INVEST criteria:
- Independent: not blocked by other stories
- Negotiable: not overly specified (invitation to conversation)
- Valuable: has clear business value
- Estimable: story points assigned, not too large
- Small: fits in a sprint (≤13 SP)
- Testable: has acceptance criteria

Returns a report with violations for the orchestrator to review.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class INVESTViolation:
    """A single INVEST criteria violation."""

    story_id: str
    story_title: str
    criteria: str  # I, N, V, E, S, T
    severity: str  # "error" or "warning"
    message: str
    suggestion: str = ""


@dataclass
class INVESTReport:
    """Full INVEST validation report."""

    total_stories: int = 0
    passed: int = 0
    violations: list[INVESTViolation] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(v.severity == "error" for v in self.violations)

    def format_markdown(self) -> str:
        """Format report as Markdown for the orchestrator."""
        lines = [
            f"# INVEST Validation Report",
            f"",
            f"**Stories checked:** {self.total_stories}",
            f"**Passed:** {self.passed}",
            f"**Violations:** {len(self.violations)}",
            f"",
        ]
        if not self.violations:
            lines.append("✅ All stories pass INVEST criteria.")
            return "\n".join(lines)

        lines.append("| Story | Criteria | Severity | Issue | Suggestion |")
        lines.append("|-------|----------|----------|-------|------------|")
        for v in self.violations:
            icon = "❌" if v.severity == "error" else "⚠️"
            lines.append(
                f"| {v.story_id} {v.story_title} | **{v.criteria}** | "
                f"{icon} {v.severity} | {v.message} | {v.suggestion} |"
            )
        return "\n".join(lines)


def validate_invest(stories: list[dict[str, Any]]) -> INVESTReport:
    """Validate a list of user stories against INVEST criteria.

    Each story dict must have:
        id, title, as_a, i_want, so_that, story_points, priority

    And optionally:
        acceptance_criteria: list of {given, when, then} dicts
        parent: str (epic ID)
        blocked_by: list[str]

    Returns an INVESTReport.
    """
    report = INVESTReport(total_stories=len(stories))
    all_ids = {s.get("id", "") for s in stories}

    for story in stories:
        sid = story.get("id", "?")
        title = story.get("title", "Untitled")
        violations_before = len(report.violations)

        _check_independent(story, all_ids, report)
        _check_negotiable(story, report)
        _check_valuable(story, report)
        _check_estimable(story, report)
        _check_small(story, report)
        _check_testable(story, report)

        if len(report.violations) == violations_before:
            report.passed += 1

    return report


# ---------------------------------------------------------------------------
# Per-criterion checks
# ---------------------------------------------------------------------------


def _check_independent(story: dict, all_ids: set[str], report: INVESTReport) -> None:
    sid = story.get("id", "?")
    title = story.get("title", "Untitled")

    blocked_by = story.get("blocked_by", [])
    if blocked_by:
        report.violations.append(
            INVESTViolation(
                story_id=sid,
                story_title=title,
                criteria="I (Independent)",
                severity="warning",
                message=f"Blocked by: {', '.join(blocked_by)}",
                suggestion="Split dependencies or reorder. Dependent stories cannot be worked on in parallel.",
            )
        )


def _check_negotiable(story: dict, report: INVESTReport) -> None:
    sid = story.get("id", "?")
    title = story.get("title", "Untitled")
    content = story.get("content", "") or story.get("i_want", "")

    # Check for overly prescriptive implementation details
    implementation_smells = [
        "use redis",
        "use postgres",
        "use kafka",
        "use docker",
        "class named",
        "function named",
        "table named",
        "column named",
        "API endpoint",
        "REST endpoint",
        "GraphQL mutation",
    ]
    for smell in implementation_smells:
        if smell.lower() in content.lower():
            report.violations.append(
                INVESTViolation(
                    story_id=sid,
                    story_title=title,
                    criteria="N (Negotiable)",
                    severity="warning",
                    message=f"Overly prescriptive: specifies '{smell}'",
                    suggestion="User stories describe WHAT and WHY, not HOW. Implementation details belong in tasks, not stories.",
                )
            )
            break  # one violation per story per criterion


def _check_valuable(story: dict, report: INVESTReport) -> None:
    sid = story.get("id", "?")
    title = story.get("title", "Untitled")
    so_that = story.get("so_that", "")

    if not so_that or len(so_that.strip()) < 10:
        report.violations.append(
            INVESTViolation(
                story_id=sid,
                story_title=title,
                criteria="V (Valuable)",
                severity="error",
                message="Missing or too vague 'so that' clause",
                suggestion="Every story must explain WHY it matters: 'So that [business value]'.",
            )
        )

    # Technical-only stories with no user value
    tech_only_patterns = [
        "refactor",
        "migrate",
        "upgrade",
        "add logging",
        "add monitoring",
    ]
    if so_that and any(
        p in title.lower() or p in (story.get("i_want", "")).lower()
        for p in tech_only_patterns
    ):
        so_that_lower = so_that.lower()
        has_user_value = any(
            w in so_that_lower
            for w in [
                "user",
                "customer",
                "business",
                "revenue",
                "retention",
                "conversion",
            ]
        )
        if not has_user_value:
            report.violations.append(
                INVESTViolation(
                    story_id=sid,
                    story_title=title,
                    criteria="V (Valuable)",
                    severity="warning",
                    message="Technical story without clear user/business value in 'so that'",
                    suggestion="Technical stories should still explain user impact: 'So that users experience faster page loads'.",
                )
            )


def _check_estimable(story: dict, report: INVESTReport) -> None:
    sid = story.get("id", "?")
    title = story.get("title", "Untitled")
    sp = story.get("story_points")

    if sp is None or sp == 0:
        report.violations.append(
            INVESTViolation(
                story_id=sid,
                story_title=title,
                criteria="E (Estimable)",
                severity="error",
                message="Story not estimated (no story points)",
                suggestion="Assign Fibonacci story points (1, 2, 3, 5, 8, 13). Use reference stories for calibration.",
            )
        )
    elif sp > 21:
        report.violations.append(
            INVESTViolation(
                story_id=sid,
                story_title=title,
                criteria="E (Estimable)",
                severity="warning",
                message=f"Very large estimate ({sp} SP) — hard to estimate accurately",
                suggestion=f"Split into smaller stories. Consider an epic with {sp // 8}+ child stories.",
            )
        )


def _check_small(story: dict, report: INVESTReport) -> None:
    sid = story.get("id", "?")
    title = story.get("title", "Untitled")
    sp = story.get("story_points")

    if sp and sp > 13:
        report.violations.append(
            INVESTViolation(
                story_id=sid,
                story_title=title,
                criteria="S (Small)",
                severity="error",
                message=f"Story too large ({sp} SP) — exceeds one sprint",
                suggestion=f"Split into {sp // 8 + 1} smaller stories of ≤8 SP each. Large stories are never done in one sprint.",
            )
        )


def _check_testable(story: dict, report: INVESTReport) -> None:
    sid = story.get("id", "?")
    title = story.get("title", "Untitled")
    ac_list = story.get("acceptance_criteria", [])

    if not ac_list or len(ac_list) == 0:
        report.violations.append(
            INVESTViolation(
                story_id=sid,
                story_title=title,
                criteria="T (Testable)",
                severity="error",
                message="No acceptance criteria defined",
                suggestion="Add Given/When/Then acceptance criteria. Without AC, you cannot prove the story is done.",
            )
        )
        return

    # Check that ACs have Given/When/Then
    for i, ac in enumerate(ac_list):
        if isinstance(ac, dict):
            missing = []
            if not ac.get("given"):
                missing.append("Given")
            if not ac.get("when"):
                missing.append("When")
            if not ac.get("then"):
                missing.append("Then")
            if missing:
                report.violations.append(
                    INVESTViolation(
                        story_id=sid,
                        story_title=title,
                        criteria="T (Testable)",
                        severity="warning",
                        message=f"AC #{i + 1} incomplete: missing {', '.join(missing)}",
                        suggestion="Every acceptance criterion needs Given/When/Then clauses.",
                    )
                )
                break  # one violation per story

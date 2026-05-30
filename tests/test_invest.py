"""Tests for INVEST validator."""

from src.agents.tools_agile import (
    INVESTReport,
    INVESTViolation,
    validate_invest,
)


def _story(**kw) -> dict:
    """Helper to create a story dict with defaults."""
    defaults = {
        "id": "US-001",
        "title": "Test Story",
        "as_a": "user",
        "i_want": "do something",
        "so_that": "I get value",
        "story_points": 5,
        "priority": "Must have",
    }
    defaults.update(kw)
    return defaults


class TestINVESTAllPass:
    """Stories that pass all INVEST criteria."""

    def test_perfect_story_passes(self):
        story = _story(
            acceptance_criteria=[
                {
                    "given": "user is logged in",
                    "when": "clicks button",
                    "then": "sees result",
                }
            ]
        )
        report = validate_invest([story])
        assert report.total_stories == 1
        assert report.passed == 1
        assert len(report.violations) == 0
        assert not report.has_errors

    def test_multiple_good_stories(self):
        stories = [
            _story(
                id="US-001",
                title="Login",
                acceptance_criteria=[
                    {
                        "given": "valid credentials",
                        "when": "submits form",
                        "then": "redirects",
                    }
                ],
            ),
            _story(
                id="US-002",
                title="Profile",
                story_points=3,
                acceptance_criteria=[
                    {"given": "logged in", "when": "views profile", "then": "sees data"}
                ],
            ),
        ]
        report = validate_invest(stories)
        assert report.passed == 2
        assert report.total_stories == 2


class TestINVESTIndependent:
    """I — Independent."""

    def test_blocked_story_warns(self):
        story = _story(blocked_by=["US-002", "US-003"])
        report = validate_invest([story])
        assert report.passed == 0
        assert any("I (Independent)" in v.criteria for v in report.violations)

    def test_unblocked_story_passes(self):
        story = _story()
        report = validate_invest([story])
        # Would fail on T (no AC), but not on I
        assert not any("I (Independent)" in v.criteria for v in report.violations)


class TestINVESTNegotiable:
    """N — Negotiable."""

    def test_prescriptive_implementation_warns(self):
        story = _story(
            i_want="use redis for caching",
            acceptance_criteria=[{"given": "x", "when": "y", "then": "z"}],
        )
        report = validate_invest([story])
        assert any("N (Negotiable)" in v.criteria for v in report.violations)

    def test_function_named_warns(self):
        story = _story(
            i_want="create a function named getUserToken",
            acceptance_criteria=[{"given": "x", "when": "y", "then": "z"}],
        )
        report = validate_invest([story])
        assert any("N (Negotiable)" in v.criteria for v in report.violations)

    def test_non_prescriptive_passes(self):
        story = _story(
            i_want="log in with email and password",
            acceptance_criteria=[{"given": "x", "when": "y", "then": "z"}],
        )
        report = validate_invest([story])
        assert not any("N (Negotiable)" in v.criteria for v in report.violations)


class TestINVESTValuable:
    """V — Valuable."""

    def test_missing_so_that_is_error(self):
        story = _story(so_that="")
        report = validate_invest([story])
        assert any(
            v.criteria == "V (Valuable)" and v.severity == "error"
            for v in report.violations
        )

    def test_refactor_without_user_value_warns(self):
        story = _story(
            title="Refactor auth",
            i_want="refactor the auth module",
            so_that="the code is cleaner",
            acceptance_criteria=[{"given": "x", "when": "y", "then": "z"}],
        )
        report = validate_invest([story])
        assert any("V (Valuable)" in v.criteria for v in report.violations)

    def test_tech_story_with_user_value_passes(self):
        story = _story(
            title="Refactor auth",
            i_want="refactor the auth module",
            so_that="users experience faster login times",
            acceptance_criteria=[{"given": "x", "when": "y", "then": "z"}],
        )
        report = validate_invest([story])
        assert not any("V (Valuable)" in v.criteria for v in report.violations)


class TestINVESTEstimable:
    """E — Estimable."""

    def test_unestimated_story_is_error(self):
        story = _story(story_points=None)
        report = validate_invest([story])
        assert any(
            v.criteria == "E (Estimable)" and v.severity == "error"
            for v in report.violations
        )

    def test_zero_points_is_error(self):
        story = _story(story_points=0)
        report = validate_invest([story])
        assert any(
            v.criteria == "E (Estimable)" and v.severity == "error"
            for v in report.violations
        )

    def test_very_large_estimate_warns(self):
        story = _story(
            story_points=34,
            acceptance_criteria=[{"given": "x", "when": "y", "then": "z"}],
        )
        report = validate_invest([story])
        assert any("E (Estimable)" in v.criteria for v in report.violations)


class TestINVESTSmall:
    """S — Small."""

    def test_too_large_story_errors(self):
        story = _story(
            story_points=21,
            acceptance_criteria=[{"given": "x", "when": "y", "then": "z"}],
        )
        report = validate_invest([story])
        assert any(
            v.criteria == "S (Small)" and v.severity == "error"
            for v in report.violations
        )

    def test_13_points_is_ok(self):
        story = _story(
            story_points=13,
            acceptance_criteria=[{"given": "x", "when": "y", "then": "z"}],
        )
        report = validate_invest([story])
        assert not any("S (Small)" in v.criteria for v in report.violations)


class TestINVESTTestable:
    """T — Testable."""

    def test_no_acceptance_criteria_is_error(self):
        story = _story()
        report = validate_invest([story])
        assert any(
            v.criteria == "T (Testable)" and v.severity == "error"
            for v in report.violations
        )

    def test_incomplete_ac_warns(self):
        story = _story(acceptance_criteria=[{"given": "x", "when": "", "then": "z"}])
        report = validate_invest([story])
        assert any("T (Testable)" in v.criteria for v in report.violations)


class TestINVESTReport:
    """Report formatting."""

    def test_all_pass_report(self):
        story = _story(acceptance_criteria=[{"given": "x", "when": "y", "then": "z"}])
        report = validate_invest([story])
        md = report.format_markdown()
        assert "Passed:" in md
        assert "✅" in md

    def test_violations_report(self):
        story = _story(so_that="")
        report = validate_invest([story])
        md = report.format_markdown()
        assert "Violations:" in md
        assert "❌" in md or "⚠️" in md

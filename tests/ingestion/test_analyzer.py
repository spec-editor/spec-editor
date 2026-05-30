"""Analyzer tests — comparing new requirements against the specification."""

from unittest.mock import MagicMock

import pytest

from src.storage.models import Element, ElementStatus, ElementSummary


class FakeStorage:
    def __init__(self, elements: list[Element]):
        self._elements = {e.id: e for e in elements}

    def list_all(self) -> list[ElementSummary]:
        return [
            ElementSummary(
                aspect=e.aspect,
                element_type=e.element_type,
                id=e.id,
                title=e.title,
                status=e.status,
            )
            for e in self._elements.values()
        ]

    def search(self, query: str) -> list[ElementSummary]:
        q = query.lower()
        return [
            ElementSummary(
                aspect=e.aspect,
                element_type=e.element_type,
                id=e.id,
                title=e.title,
                status=e.status,
            )
            for e in self._elements.values()
            if q in e.title.lower() or q in e.content.lower()
        ]

    def read_element(self, eid: str) -> Element:
        return self._elements[eid]


def _elem(
    req_id: str, title: str, content: str = "", status: str = "confirmed"
) -> Element:
    return Element(
        aspect="modules",
        element_type="module",
        id=req_id,
        title=title,
        content=content,
        status=ElementStatus(status),
    )


class TestDiffEngine:
    """DiffEngine: finds duplicates and conflicts."""

    def test_detects_duplicate_exact_match(self):
        from src.ingestion.analyzer import DiffEngine

        storage = FakeStorage(
            [
                _elem("MOD-001", "Export data to PDF", "Export reports to PDF"),
                _elem("MOD-002", "API Gateway", ""),
            ]
        )
        engine = DiffEngine(storage)

        result = engine.analyze("Export data to PDF", "Add export to PDF")
        assert result.is_duplicate is True
        assert result.matched_id == "MOD-001"

    def test_no_match_for_unrelated(self):
        from src.ingestion.analyzer import DiffEngine

        storage = FakeStorage(
            [
                _elem("MOD-001", "Export data to PDF"),
            ]
        )
        engine = DiffEngine(storage)

        result = engine.analyze(
            "Dark theme",
            "Add dark theme support for the interface",
        )
        assert result.is_duplicate is False
        assert result.matched_id is None

    def test_detects_semantic_match(self):
        """Partial word match — considered a potential duplicate."""
        from src.ingestion.analyzer import DiffEngine

        storage = FakeStorage(
            [
                _elem("MOD-001", "Export reports to PDF"),
            ]
        )
        engine = DiffEngine(storage)

        result = engine.analyze("Export to PDF", "Add export")
        # "export" and "pdf" are in both — should be a match
        assert result.is_duplicate is True


class TestConflictDetector:
    """ConflictDetector: finds contradictions."""

    def test_no_conflict_when_new_and_existing_align(self):
        from src.ingestion.analyzer import ConflictDetector

        # Existing: email notifications
        # New: add email notifications (not contradictory)
        conflicts = ConflictDetector.detect(
            new_title="Email notifications for users",
            new_description="Send email when a request is created",
            existing_title="Email notifications",
            existing_content="System sends email notifications to users",
        )
        assert len(conflicts) == 0

    def test_detects_contradiction(self):
        from src.ingestion.analyzer import ConflictDetector

        # Existing: email only
        # New: Telegram only (contradicts: different channels)
        conflicts = ConflictDetector.detect(
            new_title="Notifications via Telegram",
            new_description="Send notifications only via Telegram",
            existing_title="Email notifications",
            existing_content="All notifications are sent ONLY by email",
        )
        # Key words contradict: telegram vs email-only
        assert len(conflicts) > 0

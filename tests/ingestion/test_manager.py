"""Ingestion manager tests — deprecate / restore."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.ingestion.manager import (
    _build_element_list,
    _parse_llm_response,
    deprecate_from_file,
    restore_elements,
)
from src.providers.base import LLMResponse, LLMUsage
from src.storage.models import Element, ElementStatus, ElementSummary


class FakeStorage:
    def __init__(self, elements: list[Element] | None = None):
        self._elements = {e.id: e for e in (elements or [])}

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

    def read_element(self, element_id: str) -> Element:
        return self._elements[element_id]

    def write_element(self, element: Element) -> None:
        self._elements[element.id] = element


def _elem(req_id: str, title: str, status: str = "confirmed") -> Element:
    return Element(
        aspect="modules",
        element_type="module",
        id=req_id,
        title=title,
        status=ElementStatus(status),
    )


class TestParseResponse:
    def test_parses_json_array(self):
        assert _parse_llm_response('["NFR-001", "MOD-002"]') == ["NFR-001", "MOD-002"]

    def test_parses_with_extra_text(self):
        r = _parse_llm_response('Вот: ["NFR-001"]\nГотово.')
        assert r == ["NFR-001"]

    def test_empty_array(self):
        assert _parse_llm_response("[]") == []

    def test_no_json(self):
        assert _parse_llm_response("Нет совпадений") == []

    def test_invalid_json(self):
        assert _parse_llm_response("[invalid}") == []


class TestBuildElementList:
    def test_filters_confirmed_and_reviewed(self):
        s = FakeStorage(
            [
                _elem("A", "Alpha", "confirmed"),
                _elem("B", "Beta", "reviewed"),
                _elem("C", "Gamma", "draft"),
                _elem("D", "Delta", "deprecated"),
            ]
        )
        r = _build_element_list(s)
        assert "A: Alpha" in r
        assert "B: Beta" in r
        assert "C" not in r
        assert "D" not in r

    def test_empty(self):
        assert _build_element_list(FakeStorage([])) == ""


class TestDeprecateFromFile:
    @pytest.mark.asyncio
    async def test_dry_run_finds_matches(self):
        s = FakeStorage(
            [
                _elem("NFR-pdf", "Экспорт в PDF"),
                _elem("MOD-api", "API Gateway"),
                _elem("NFR-tg", "Уведомления Telegram", "reviewed"),
            ]
        )
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LLMResponse(
                content='["NFR-pdf", "NFR-tg"]',
                usage=LLMUsage(),
            )
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Убрать экспорт и телеграм")
            f.flush()
            r = await deprecate_from_file(s, provider, Path(f.name), dry_run=True)
        assert r["dry_run"]
        assert len(r["deprecated"]) == 2
        ids = {d["id"] for d in r["deprecated"]}
        assert ids == {"NFR-pdf", "NFR-tg"}

    @pytest.mark.asyncio
    async def test_dry_run_does_not_modify(self):
        s = FakeStorage([_elem("NFR-pdf", "Экспорт")])
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LLMResponse(
                content='["NFR-pdf"]',
                usage=LLMUsage(),
            )
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Убрать")
            f.flush()
            await deprecate_from_file(s, provider, Path(f.name), dry_run=True)
        assert s.read_element("NFR-pdf").status == ElementStatus.CONFIRMED

    @pytest.mark.asyncio
    async def test_real_deprecate(self):
        s = FakeStorage([_elem("NFR-pdf", "Экспорт")])
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LLMResponse(
                content='["NFR-pdf"]',
                usage=LLMUsage(),
            )
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Убрать")
            f.flush()
            await deprecate_from_file(s, provider, Path(f.name), dry_run=False)
        assert s.read_element("NFR-pdf").status == ElementStatus.DEPRECATED

    @pytest.mark.asyncio
    async def test_no_matches(self):
        s = FakeStorage([_elem("MOD-api", "API")])
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LLMResponse(
                content="[]",
                usage=LLMUsage(),
            )
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Убрать телеграм")
            f.flush()
            r = await deprecate_from_file(s, provider, Path(f.name))
        assert r["deprecated"] == []

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        r = await deprecate_from_file(FakeStorage(), MagicMock(), Path("/no/file"))
        assert "error" in r

    @pytest.mark.asyncio
    async def test_handles_nonexistent_ids(self):
        s = FakeStorage([_elem("MOD-api", "API")])
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LLMResponse(
                content='["MOD-api", "FAKE-999"]',
                usage=LLMUsage(),
            )
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Убрать")
            f.flush()
            r = await deprecate_from_file(s, provider, Path(f.name))
        assert len(r["deprecated"]) == 1
        assert "FAKE-999" in r["not_found"]


class TestRestoreElements:
    def test_restore_deprecated_to_draft(self):
        s = FakeStorage([_elem("NFR-001", "Test", "deprecated")])
        r = restore_elements(s, ["NFR-001"])
        assert len(r["restored"]) == 1
        assert s.read_element("NFR-001").status == ElementStatus.DRAFT

    def test_restore_confirmed_ignored(self):
        s = FakeStorage([_elem("NFR-001", "Test", "confirmed")])
        r = restore_elements(s, ["NFR-001"])
        assert len(r["restored"]) == 0
        assert len(r["not_deprecated"]) == 1

    def test_restore_nonexistent(self):
        r = restore_elements(FakeStorage(), ["FAKE-999"])
        assert "FAKE-999" in r["not_found"]

    def test_restore_multiple(self):
        s = FakeStorage(
            [
                _elem("A", "Alpha", "deprecated"),
                _elem("B", "Beta", "deprecated"),
                _elem("C", "Gamma", "confirmed"),
            ]
        )
        r = restore_elements(s, ["A", "B", "C"])
        assert len(r["restored"]) == 2
        assert len(r["not_deprecated"]) == 1

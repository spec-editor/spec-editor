"""Preprocessor tests — classification and extraction of requirements."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.providers.base import LLMResponse, LLMUsage


class FakeProvider:
    """LLM provider with predictable responses."""

    def __init__(self, responses: list[str]):
        self._responses = responses
        self._idx = 0
        self.calls: list[list] = []

    async def complete(self, messages, tools=None, **kwargs):
        self.calls.append(messages)
        resp = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return LLMResponse(content=resp, usage=LLMUsage())

    def supports_tools(self) -> bool:
        return True


class TestRequirementClassifier:
    """RequirementClassifier: determines whether text is a requirement."""

    def test_classifies_requirement(self):
        from src.ingestion.preprocessor import RequirementClassifier

        provider = FakeProvider(["ДА"])
        classifier = RequirementClassifier(provider)

        result = classifier.classify(
            "Нужно добавить экспорт данных в PDF для всех отчётов"
        )
        assert result.is_requirement is True
        assert result.confidence > 0.5

    def test_classifies_spam(self):
        from src.ingestion.preprocessor import RequirementClassifier

        provider = FakeProvider(["НЕТ"])
        classifier = RequirementClassifier(provider)

        result = classifier.classify("Привет! Как дела?")
        assert result.is_requirement is False
        assert result.confidence < 0.5

    def test_extracts_confidence_from_response(self):
        from src.ingestion.preprocessor import RequirementClassifier

        provider = FakeProvider(["YES (confidence: 0.9)"])
        classifier = RequirementClassifier(provider)

        result = classifier.classify("Add dark theme support")
        assert result.is_requirement is True
        assert result.confidence == 0.9

    def test_handles_ambiguous(self):
        from src.ingestion.preprocessor import RequirementClassifier

        provider = FakeProvider(["НЕ УВЕРЕН"])
        classifier = RequirementClassifier(provider)

        result = classifier.classify("Может быть добавить поиск?")
        assert result.is_requirement is False


class TestFactExtractor:
    """FactExtractor: extracts structured facts from a requirement."""

    def test_extracts_title_and_description(self):
        from src.ingestion.preprocessor import FactExtractor

        provider = FakeProvider(
            [
                '{"title": "Экспорт в PDF", "description": "Пользователи хотят '
                'экспортировать отчёты в PDF формат", "aspect": "user_scenarios", '
                '"priority": "medium"}',
            ]
        )
        extractor = FactExtractor(provider)

        fact = extractor.extract("Нужно добавить экспорт данных в PDF для всех отчётов")
        assert fact.title == "Экспорт в PDF"
        assert "PDF" in fact.description
        assert fact.aspect == "user_scenarios"
        assert fact.priority == "medium"

    def test_extract_handles_invalid_json(self):
        from src.ingestion.preprocessor import FactExtractor

        provider = FakeProvider(["not json at all, just Экспорт в PDF описание"])
        extractor = FactExtractor(provider)

        fact = extractor.extract("Добавить экспорт")
        assert fact.title != ""


class TestSourcePreprocessor:
    """SourcePreprocessor: scans source/, filters spam, saves."""

    def test_skips_already_processed_files(self, tmp_path):
        from src.ingestion.preprocessor import SourcePreprocessor

        source_dir = tmp_path / "project"
        raw_dir = source_dir / "source_raw"
        raw_dir.mkdir(parents=True)
        (raw_dir / "req1.txt").write_text("Добавить экспорт в PDF")

        classifier = MagicMock()
        # Mock for batch classification
        classifier.classify_batch = MagicMock(
            return_value={
                "req1.txt": MagicMock(is_requirement=True, confidence=0.9),
            }
        )
        extractor = MagicMock()
        extractor.extract = MagicMock(
            return_value=MagicMock(
                title="Export",
                description="Export to PDF",
                aspect="user_scenarios",
                priority="medium",
            )
        )

        preprocessor = SourcePreprocessor(
            source_dir=source_dir,
            output_dir=tmp_path / "ingestion",
            classifier=classifier,
            extractor=extractor,
        )

        results = preprocessor.process()
        # Only req1.txt should be processed (filtered_req1.txt skipped)
        assert len(results) == 1
        assert results[0].source_file == "req1.txt"

    def test_skips_spam(self, tmp_path):
        from src.ingestion.preprocessor import SourcePreprocessor

        source_dir = tmp_path / "project"
        raw_dir = source_dir / "source_raw"
        raw_dir.mkdir(parents=True)
        (raw_dir / "spam.txt").write_text("Привет! Как дела?")

        classifier = MagicMock()
        classifier.classify_batch = MagicMock(
            return_value={
                "spam.txt": MagicMock(is_requirement=False, confidence=0.1),
            }
        )
        extractor = MagicMock()

        preprocessor = SourcePreprocessor(
            source_dir=source_dir,
            output_dir=tmp_path / "ingestion",
            classifier=classifier,
            extractor=extractor,
        )

        results = preprocessor.process()
        assert len(results) == 1
        assert results[0].is_spam is True
        # Spam marked
        assert (raw_dir / "_spam_spam.txt").exists()

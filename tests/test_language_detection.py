"""Tests for language auto-detection in main.py."""

from pathlib import Path

import pytest

from src.main import _auto_detect_language


class TestAutoDetectLanguage:
    """Language auto-detection from source documents."""

    def test_russian_detected(self, tmp_path, monkeypatch):
        """Cyrillic > 30% → Russian prompts."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "requirements.md").write_text(
            "Бизнес-требования: Платформа управления сетью сайтов v1.0. "
            "Основная цель: создание централизованной платформы для генерации, "
            "масштабирования и поддержки сети сайтов. Критерии успеха: "
            "скорость запуска. Роли: Оператор, Модератор, SEO-специалист.",
            encoding="utf-8",
        )

        from unittest.mock import MagicMock

        settings = MagicMock()
        settings.prompt_language = "en"

        called_lang = []

        def fake_set_lang(lang):
            called_lang.append(lang)

        monkeypatch.setattr("src.agents.prompts.set_prompt_language", fake_set_lang)

        _auto_detect_language(tmp_path, settings)
        assert called_lang == ["ru"], f"Expected 'ru', got {called_lang}"

    def test_english_not_changed(self, tmp_path, monkeypatch):
        """English text → language stays unchanged."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "requirements.md").write_text(
            "Business Requirements: Site Matrix Platform v1.0. "
            "The main goal is creating a centralized platform for generating "
            "and scaling a network of sites. Success criteria: launch speed.",
            encoding="utf-8",
        )

        from unittest.mock import MagicMock

        settings = MagicMock()
        settings.prompt_language = "en"

        called_lang = []

        def fake_set_lang(lang):
            called_lang.append(lang)

        monkeypatch.setattr("src.agents.prompts.set_prompt_language", fake_set_lang)

        _auto_detect_language(tmp_path, settings)
        assert called_lang == [], (
            f"Should not change language for English text, got {called_lang}"
        )

    def test_no_source_dir(self, tmp_path, monkeypatch):
        """No source/ directory → no language change."""
        from unittest.mock import MagicMock

        settings = MagicMock()
        settings.prompt_language = "en"

        called_lang = []

        def fake_set_lang(lang):
            called_lang.append(lang)

        monkeypatch.setattr("src.agents.prompts.set_prompt_language", fake_set_lang)

        _auto_detect_language(tmp_path, settings)
        assert called_lang == []

    def test_mixed_russian_dominates(self, tmp_path, monkeypatch):
        """Mixed text where Cyrillic > 30% of alpha chars → Russian."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        # ~50% Cyrillic, 50% Latin (some English tech terms)
        (source_dir / "requirements.md").write_text(
            "API должно поддерживать REST и GraphQL. "
            "База данных PostgreSQL с репликацией. "
            "Docker контейнеры для деплоя. "
            "Мониторинг через Prometheus и Grafana. "
            "Требования к безопасности: JWT, RBAC, CORS.",
            encoding="utf-8",
        )

        from unittest.mock import MagicMock

        settings = MagicMock()
        settings.prompt_language = "en"

        called_lang = []

        def fake_set_lang(lang):
            called_lang.append(lang)

        monkeypatch.setattr("src.agents.prompts.set_prompt_language", fake_set_lang)

        _auto_detect_language(tmp_path, settings)
        assert called_lang == ["ru"], (
            f"Expected 'ru' for mixed Russian-dominant text, got {called_lang}"
        )

    def test_empty_source_dir(self, tmp_path, monkeypatch):
        """Empty source/ → no language change."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()

        from unittest.mock import MagicMock

        settings = MagicMock()
        settings.prompt_language = "en"

        called_lang = []

        def fake_set_lang(lang):
            called_lang.append(lang)

        monkeypatch.setattr("src.agents.prompts.set_prompt_language", fake_set_lang)

        _auto_detect_language(tmp_path, settings)
        assert called_lang == []

    def test_multiple_files(self, tmp_path, monkeypatch):
        """Language detection across multiple source files."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "business.md").write_text(
            "Бизнес-требования к платформе. Функциональные требования.",
            encoding="utf-8",
        )
        (source_dir / "technical.md").write_text(
            "Технический дизайн системы. Архитектура и компоненты.",
            encoding="utf-8",
        )

        from unittest.mock import MagicMock

        settings = MagicMock()
        settings.prompt_language = "en"

        called_lang = []

        def fake_set_lang(lang):
            called_lang.append(lang)

        monkeypatch.setattr("src.agents.prompts.set_prompt_language", fake_set_lang)

        _auto_detect_language(tmp_path, settings)
        assert called_lang == ["ru"]

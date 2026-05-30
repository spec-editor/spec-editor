"""Tests for PromptLoader — multilingual agent prompts."""

import tempfile
from pathlib import Path

import yaml

from src.config.prompt_loader import PromptLoader


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


SAMPLE_EN = {
    "spec_agent": "You are a senior analyst.\nMethodology: {methodology_description}",
    "orchestrator": "You are an orchestrator.\nEvaluate round {round_num}.",
    "classifier": "Is this a requirement? Answer YES or NO.",
    "extractor": "Extract structured requirement from:\n{text}",
    "deprecation": "Find deprecated IDs in:\n{requirements}",
    "diff_engine": "Compare new vs existing requirements.",
    "helper_spawn": "You are a helper. Role: {role}. Task: {task}.",
}

SAMPLE_RU = {
    "spec_agent": "Ты — старший аналитик.\nМетодология: {methodology_description}",
    "orchestrator": "Ты — оркестратор.\nОцени раунд {round_num}.",
    "classifier": "Это требование? Ответь ДА или НЕТ.",
    "extractor": "Извлеки требование из:\n{text}",
    "deprecation": "Найди ID для deprecate:\n{requirements}",
    "diff_engine": "Сравни новые и существующие требования.",
    "helper_spawn": "Ты — помощник. Роль: {role}. Задача: {task}.",
}


class TestPromptLoaderInit:
    def test_loads_default_language(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        _write_yaml(prompts_dir / "en.yaml", SAMPLE_EN)

        loader = PromptLoader(prompts_dir=prompts_dir)
        assert loader.language == "en"

    def test_loads_specified_language(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        _write_yaml(prompts_dir / "en.yaml", SAMPLE_EN)
        _write_yaml(prompts_dir / "ru.yaml", SAMPLE_RU)

        loader = PromptLoader(prompts_dir=prompts_dir, language="ru")
        assert loader.language == "ru"

    def test_falls_back_to_en_when_missing(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        _write_yaml(prompts_dir / "en.yaml", SAMPLE_EN)

        loader = PromptLoader(prompts_dir=prompts_dir, language="fr")
        # Falls back to en silently
        assert loader.language == "fr"

    def test_raises_when_no_en_available(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        with __import__("pytest").raises(FileNotFoundError):
            PromptLoader(prompts_dir=prompts_dir)


class TestPromptLoaderGet:
    def test_get_prompt_english(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        _write_yaml(prompts_dir / "en.yaml", SAMPLE_EN)

        loader = PromptLoader(prompts_dir=prompts_dir)
        result = loader.get("spec_agent")
        assert "You are a senior analyst" in result
        assert "{methodology_description}" in result

    def test_get_prompt_russian(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        _write_yaml(prompts_dir / "en.yaml", SAMPLE_EN)
        _write_yaml(prompts_dir / "ru.yaml", SAMPLE_RU)

        loader = PromptLoader(prompts_dir=prompts_dir, language="ru")
        result = loader.get("spec_agent")
        assert "старший аналитик" in result

    def test_get_with_format_kwargs(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        _write_yaml(prompts_dir / "en.yaml", SAMPLE_EN)

        loader = PromptLoader(prompts_dir=prompts_dir)
        result = loader.get("spec_agent", methodology_description="Waterfall v1.0")
        assert "Waterfall v1.0" in result
        assert "You are a senior analyst" in result

    def test_get_missing_key_falls_back_to_en(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        en_data = dict(SAMPLE_EN)
        ru_data = dict(SAMPLE_RU)
        del ru_data["diff_engine"]  # missing in RU
        _write_yaml(prompts_dir / "en.yaml", en_data)
        _write_yaml(prompts_dir / "ru.yaml", ru_data)

        loader = PromptLoader(prompts_dir=prompts_dir, language="ru")
        result = loader.get("diff_engine")
        assert "Compare new vs existing" in result  # en fallback

    def test_get_nonexistent_key_raises(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        _write_yaml(prompts_dir / "en.yaml", SAMPLE_EN)

        loader = PromptLoader(prompts_dir=prompts_dir)
        with __import__("pytest").raises(KeyError):
            loader.get("nonexistent_key")


class TestPromptLoaderLanguages:
    def test_list_available_languages(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        _write_yaml(prompts_dir / "en.yaml", SAMPLE_EN)
        _write_yaml(prompts_dir / "ru.yaml", SAMPLE_RU)
        _write_yaml(prompts_dir / "es.yaml", {})

        loader = PromptLoader(prompts_dir=prompts_dir)
        langs = loader.list_languages()
        assert "en" in langs
        assert "ru" in langs
        assert "es" in langs

    def test_set_language(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        _write_yaml(prompts_dir / "en.yaml", SAMPLE_EN)
        _write_yaml(prompts_dir / "ru.yaml", SAMPLE_RU)

        loader = PromptLoader(prompts_dir=prompts_dir)
        loader.set_language("ru")
        assert loader.language == "ru"
        result = loader.get("spec_agent")
        assert "старший аналитик" in result


class TestPromptKeys:
    def test_required_keys_present(self, tmp_path):
        """Verify all required prompt keys are in en.yaml."""
        prompts_dir = tmp_path / "prompts"
        _write_yaml(prompts_dir / "en.yaml", SAMPLE_EN)

        loader = PromptLoader(prompts_dir=prompts_dir)
        required = ["spec_agent", "orchestrator", "classifier", "extractor"]
        for key in required:
            result = loader.get(key)
            assert result, f"Key '{key}' should not be empty"


class TestRealPromptFiles:
    """Verify the bundled prompt YAML files are valid."""

    REQUIRED_KEYS = [
        "spec_agent",
        "orchestrator",
        "orchestrator_eval",
        "classifier",
        "classifier_batch",
        "extractor",
        "deprecation",
        "helper_spawn",
        "colleague_response",
        "answer_inject",
    ]

    def test_all_real_languages_load(self):
        """All 5 language files load without errors."""
        loader = PromptLoader()  # uses prompts/ in project root
        langs = loader.list_languages()
        assert "en" in langs
        assert "ru" in langs
        assert "es" in langs
        assert "fr" in langs
        assert "de" in langs

    def test_all_keys_present_in_all_languages(self):
        """Every key exists in every language file."""
        loader = PromptLoader()
        for lang in ["en", "ru", "es", "fr", "de"]:
            loader.set_language(lang)
            for key in self.REQUIRED_KEYS:
                result = loader.get(key)
                assert result, f"{lang}: key '{key}' is empty"
                assert len(result) > 20, (
                    f"{lang}: key '{key}' too short ({len(result)} chars)"
                )

    def test_format_variables_consistent(self):
        """All languages have the same format variables."""
        import re

        loader = PromptLoader()
        en_vars: dict[str, set[str]] = {}
        for key in self.REQUIRED_KEYS:
            template = loader.get(key)
            en_vars[key] = set(re.findall(r"\{(\w+)\}", template))

        for lang in ["ru", "es", "fr", "de"]:
            loader.set_language(lang)
            for key in self.REQUIRED_KEYS:
                template = loader.get(key)
                lang_vars = set(re.findall(r"\{(\w+)\}", template))
                assert en_vars[key] == lang_vars, (
                    f"{lang}/{key}: format vars differ. "
                    f"EN: {en_vars[key]}, {lang.upper()}: {lang_vars}"
                )

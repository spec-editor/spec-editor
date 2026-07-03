"""Multilingual prompt loader.

Loads LLM agent prompts from YAML language files in prompts/.
Falls back to English when a key or language is missing.
"""

from pathlib import Path

import yaml

from src.config import get_logger
from src.config._data_path import data_path

logger = get_logger(__name__)


class PromptLoader:
    """Loads prompts by key and language with format-string interpolation.

    Directory structure:
        prompts/
            en.yaml    (canonical, always required)
            ru.yaml
            es.yaml
            fr.yaml
            de.yaml

    Each YAML file is a flat dict of key → prompt_template.
    Templates use Python str.format() syntax: {variable_name}.
    """

    def __init__(
        self,
        prompts_dir: Path | None = None,
        language: str = "en",
    ) -> None:
        if prompts_dir is None:
            prompts_dir = data_path("prompts")
        self._prompts_dir = Path(prompts_dir)
        self._language = language
        self._cache: dict[str, dict[str, str]] = {}

        # English is always required
        en_path = self._prompts_dir / "en.yaml"
        if not en_path.exists():
            raise FileNotFoundError(
                f"Canonical prompt file not found: {en_path}. "
                f"At least en.yaml is required."
            )
        self._cache["en"] = self._load_file(en_path)

        # Load requested language if not English
        if language != "en":
            self._load_language(language)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def language(self) -> str:
        return self._language

    def set_language(self, language: str) -> None:
        """Switch to a different language at runtime."""
        if language != self._language:
            self._language = language
            self._load_language(language)

    def get(self, key: str, **kwargs: str) -> str:
        """Get a prompt template by key, formatted with kwargs.

        Looks in the current language first, falls back to English.

        Raises:
            KeyError: key not found in any language
        """
        # Try current language
        prompts = self._cache.get(self._language, {})
        template = prompts.get(key)

        # Fall back to English
        if template is None:
            en_prompts = self._cache.get("en", {})
            template = en_prompts.get(key)

        if template is None:
            raise KeyError(
                f"Prompt key '{key}' not found in '{self._language}' or 'en'"
            )

        if kwargs:
            return template.format(**kwargs)
        return template

    def list_languages(self) -> list[str]:
        """List all available language codes."""
        langs = []
        for path in sorted(self._prompts_dir.glob("*.yaml")):
            langs.append(path.stem)
        return langs

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_language(self, language: str) -> None:
        """Load a language file, falling back to en for missing keys."""
        path = self._prompts_dir / f"{language}.yaml"
        if path.exists():
            prompts = self._load_file(path)
            # Fill missing keys from English
            en = self._cache.get("en", {})
            for key in en:
                if key not in prompts:
                    prompts[key] = en[key]
            self._cache[language] = prompts
        else:
            logger.warning(
                "prompt_language_missing",
                language=language,
                fallback="en",
            )
            # Use English directly
            self._cache[language] = dict(self._cache.get("en", {}))

    @staticmethod
    def _load_file(path: Path) -> dict[str, str]:
        """Load a YAML prompt file."""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return {str(k): str(v) for k, v in data.items()}

"""Agent prompts — loaded from multilingual YAML files.

All prompts live in prompts/*.yaml, not in Python code.
Use set_prompt_language() to switch languages at runtime.
Use get_spec_agent_prompt() / get_orchestrator_prompt() for lazy loading.
"""

from importlib import resources
from pathlib import Path

from src.config.prompt_loader import PromptLoader

_PROMPTS_DIR = resources.files("data") / "prompts"
_loader = PromptLoader(_PROMPTS_DIR, language="en")


def set_prompt_language(language: str) -> None:
    """Switch prompt language at runtime (e.g. 'ru', 'en', 'es').

    All subsequent calls to get_*_prompt() will return prompts
    in the new language. Already-loaded modules are unaffected
    because they use the lazy getter functions.
    """
    global _loader
    _loader = PromptLoader(_PROMPTS_DIR, language=language)


def get_spec_agent_prompt() -> str:
    """Get the spec agent system prompt in the current language."""
    return _loader.get("spec_agent")


def get_orchestrator_prompt() -> str:
    """Get the orchestrator system prompt in the current language."""
    return _loader.get("orchestrator")


def get_cross_aspect_prompt() -> str:
    """Get the cross-aspect relationship agent prompt."""
    return _loader.get("cross_aspect_agent")


# Backward-compatible module-level constants (used at import time,
# before language detection — always English)
SPEC_AGENT_SYSTEM_PROMPT = _loader.get("spec_agent")
ORCHESTRATOR_SYSTEM_PROMPT = _loader.get("orchestrator")

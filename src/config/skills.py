"""Loading and management of agent skills (skills.yaml)."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Skill(BaseModel):
    """Agent skill — prompt and toolset."""

    name: str
    description: str = ""
    prompt: str = ""  # if empty — use the default from prompts.py
    tools: list[str] = Field(default_factory=list)  # tool names (if empty — all)


class SkillsRegistry:
    """Registry of skills loaded from skills.yaml."""

    def __init__(self, path: Path | None = None) -> None:
        self._skills: dict[str, Skill] = {}
        if path and path.exists():
            self._load(path)

    def _load(self, path: Path) -> None:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        for skill_data in data.get("skills", []):
            skill = Skill(**skill_data)
            self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def has(self, name: str) -> bool:
        return name in self._skills

    def __len__(self) -> int:
        return len(self._skills)

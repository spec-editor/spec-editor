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
    """Registry of skills loaded from YAML file(s) or directory.

    Supports three layouts:

    * Single file: ``skills.yaml`` at project root (legacy).
    * Directory:   ``skills/*.yaml`` — each file contributes skills.
    * Mixed:       directory + legacy file merged.

    Files are merged; duplicate skill names are overwritten
    (last wins).
    """

    def __init__(self, path: Path | list[Path] | None = None) -> None:
        self._skills: dict[str, Skill] = {}
        paths: list[Path] = []
        if isinstance(path, list):
            paths = path
        elif path is not None:
            paths = [path]

        for p in paths:
            if not p.exists():
                continue
            if p.is_dir():
                for yaml_file in sorted(p.glob("*.yaml")):
                    self._load_file(yaml_file)
            else:
                self._load_file(p)

    def _load_file(self, path: Path) -> None:
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

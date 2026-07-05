"""AgentRole tests — agent role (prompt + tools + writable)."""

import pytest

from src.agents.role import AgentRole
from src.config.skills import Skill


class TestAgentRole:
    """AgentRole: links Skill with agent role."""

    def test_default_role_has_all_tools(self):
        """Default role has all tools and writable=True."""
        role = AgentRole(name="test", writable=True)
        assert role.name == "test"
        assert role.writable is True
        assert role.prompt == ""  # standard prompt is used

    def test_readonly_role(self):
        """Read-only role."""
        role = AgentRole(name="orchestrator", writable=False)
        assert role.writable is False

    def test_role_from_skill_uses_skill_prompt(self):
        """If a prompt is set in the skill — it is used."""
        skill = Skill(name="expert", prompt="You are an expert", tools=[])
        role = AgentRole.from_skill(skill, writable=True)
        assert role.prompt == "You are an expert"
        assert role.name == "expert"

    def test_role_from_skill_empty_prompt_falls_back(self):
        """If the skill prompt is empty — the default is used."""
        skill = Skill(name="analyst", prompt="", tools=[])
        role = AgentRole.from_skill(skill, writable=True, default_prompt="DEFAULT")
        assert role.prompt == "DEFAULT"

    def test_role_tools_subset_when_skill_specifies(self):
        """A skill can restrict the tool set."""
        skill = Skill(name="reader", prompt="", tools=["read_element", "list_aspect"])
        role = AgentRole.from_skill(skill, writable=False)
        assert role._allowed_tools == {"read_element", "list_aspect"}

    def test_role_tools_all_when_skill_empty(self):
        """If the skill doesn't specify tools — all are available."""
        skill = Skill(name="full", prompt="", tools=[])
        role = AgentRole.from_skill(skill, writable=True)
        assert role._allowed_tools is None  # None = все

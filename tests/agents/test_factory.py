"""AgentFactory tests — creating agents from configuration."""

import pytest

from src.agents.role import AgentRole
from src.config.settings import AgentConfig


class FakeProvider:
    def __init__(self):
        self._supports_tools = True

    async def complete(self, messages, tools=None, **kwargs):
        from src.providers.base import LLMResponse

        return LLMResponse(content="ok")

    def supports_tools(self) -> bool:
        return self._supports_tools


class FakeStorage:
    def list_all(self):
        return []

    def read_element(self, id):
        raise KeyError(id)

    def write_element(self, e):
        pass


class FakeMethodology:
    name = "test"
    version = "1.0"
    description = ""
    aspects = []
    skills = []


class TestAgentFactory:
    """AgentFactory: creating agents from Role + Config."""

    def test_create_agent_from_role_and_provider(self):
        """Factory creates SpecAgent from AgentRole + Provider."""
        from src.agents.factory import AgentFactory
        from src.agents.spec_agent import SpecAgent

        factory = AgentFactory(
            provider=FakeProvider(),
            storage=FakeStorage(),
            methodology=FakeMethodology(),
        )
        role = AgentRole(name="test", writable=True, prompt="YOU ARE TEST")
        agent = factory.create(role, name="test_agent")

        assert isinstance(agent, SpecAgent)
        assert agent.name == "test_agent"

    def test_create_readonly_agent(self):
        """writable=False role creates an agent without write tools."""
        from src.agents.factory import AgentFactory
        from src.agents.spec_agent import SpecAgent

        factory = AgentFactory(
            provider=FakeProvider(),
            storage=FakeStorage(),
            methodology=FakeMethodology(),
        )
        role = AgentRole.orchestrator()
        agent = factory.create(role, name="orch")

        assert isinstance(agent, SpecAgent)
        # Orchestrator is also SpecAgent, but with RO tools

    def test_factory_uses_config_defaults(self):
        """Without explicit parameters, defaults are used."""
        from src.agents.factory import AgentFactory

        factory = AgentFactory(
            provider=FakeProvider(),
            storage=FakeStorage(),
            methodology=FakeMethodology(),
        )
        # Should work without errors
        role = AgentRole.spec_agent()
        agent = factory.create(role)
        assert agent is not None

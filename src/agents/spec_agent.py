"""Requirements developer agent (Agent 1 / Agent 2)."""

from src.agents.base import BaseAgent
from src.agents.prompts import get_spec_agent_prompt
from src.agents.role import AgentRole
from src.agents.tools import build_all_handlers, get_tool_definitions
from src.config.methodology import Methodology, format_methodology
from src.providers.base import LLMProvider
from src.storage.adapter import StorageAdapter


class SpecAgent(BaseAgent):
    """Requirements developer agent.

    Created from AgentRole — the role defines writable and prompt.
    """

    def __init__(
        self,
        name: str,
        provider: LLMProvider,
        storage: StorageAdapter,
        methodology: Methodology,
        source_dir: str | None = None,
        spawner=None,
        max_llm_calls: int = 30,
        token_budget: int = 50000,
        role: AgentRole | None = None,
    ) -> None:
        # Role defines writable and prompt
        if role is None:
            role = AgentRole.spec_agent(name)

        tools = get_tool_definitions(writable=role.writable)
        tool_handlers = build_all_handlers(
            storage, methodology, source_dir, spawner, agent_for_compact=self
        )

        methodology_text = format_methodology(methodology)
        system_prompt = (
            role.prompt.format(methodology_description=methodology_text)
            if role.prompt
            else get_spec_agent_prompt().format(
                methodology_description=methodology_text
            )
        )

        self._my_methodology = methodology
        self._my_source_dir = source_dir

        super().__init__(
            name=name,
            provider=provider,
            system_prompt=system_prompt,
            tools=tools,
            tool_handlers=tool_handlers,
            max_llm_calls=max_llm_calls,
            token_budget=token_budget,
        )

    def _get_methodology(self):
        return self._my_methodology

    def _get_source_dir(self):
        return self._my_source_dir

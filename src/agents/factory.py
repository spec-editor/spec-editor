"""Agent factory — creation from AgentRole + Provider."""

from src.agents.role import AgentRole
from src.agents.spec_agent import SpecAgent
from src.config.methodology import Methodology
from src.providers.base import LLMProvider
from src.storage.adapter import StorageAdapter


class AgentFactory:
    """Creates agents from role, provider, and storage."""

    def __init__(
        self,
        provider: LLMProvider,
        storage: StorageAdapter,
        methodology: Methodology,
        source_dir: str | None = None,
        max_llm_calls: int = 30,
        token_budget: int = 50000,
    ) -> None:
        self._provider = provider
        self._storage = storage
        self._methodology = methodology
        self._source_dir = source_dir
        self._max_llm_calls = max_llm_calls
        self._token_budget = token_budget

    def create(self, role: AgentRole, name: str | None = None) -> SpecAgent:
        """Create an agent with the specified role."""
        return SpecAgent(
            name=name or role.name,
            provider=self._provider,
            storage=self._storage,
            methodology=self._methodology,
            source_dir=self._source_dir,
            max_llm_calls=self._max_llm_calls,
            token_budget=self._token_budget,
            role=role,
        )

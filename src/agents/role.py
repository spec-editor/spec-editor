"""Agent role — prompt, tools, write permissions."""

from src.agents.prompts import ORCHESTRATOR_SYSTEM_PROMPT, SPEC_AGENT_SYSTEM_PROMPT
from src.config.skills import Skill
from src.tracing import implements


class AgentRole:
    """Agent role: name, prompt, tool set, write permissions.

    Created either directly or from Skill + default_prompt.
    """

    def __init__(
        self,
        name: str,
        writable: bool = True,
        prompt: str = "",
        allowed_tools: set[str] | None = None,
    ) -> None:
        self.name = name
        self.writable = writable
        self.prompt = prompt
        self._allowed_tools = allowed_tools  # None = «TRANSLATED» «TRANSLATED»

    @classmethod
    def from_skill(
        cls,
        skill: Skill,
        writable: bool = True,
        default_prompt: str = "",
    ) -> "AgentRole":
        """Create a role from a skill.

        If the skill has a prompt set — it is used.
        Otherwise — default_prompt (usually SPEC_AGENT_SYSTEM_PROMPT).
        """
        prompt = skill.prompt if skill.prompt else default_prompt
        allowed = set(skill.tools) if skill.tools else None
        return cls(
            name=skill.name, writable=writable, prompt=prompt, allowed_tools=allowed
        )

    @classmethod
    def spec_agent(cls, name: str = "spec_agent") -> "AgentRole":
        """Standard role of a requirements developer agent."""
        return cls(name=name, writable=True, prompt=SPEC_AGENT_SYSTEM_PROMPT)

    @classmethod
    @implements("MOD-001-C3")
    def orchestrator(cls) -> "AgentRole":
        """Orchestrator role — read-only."""
        return cls(
            name="orchestrator", writable=False, prompt=ORCHESTRATOR_SYSTEM_PROMPT
        )

"""Application settings via Pydantic BaseSettings."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ------------------------------------------------------------------
# Agent configuration
# ------------------------------------------------------------------


class AgentConfig(BaseModel):
    """Configuration for a single agent."""

    provider: str = Field(default="openai")
    model: str = Field(default="gpt-4o")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096)


class AgentsConfig(BaseModel):
    """Configuration for all agents and dialogue parameters."""

    agent_1: AgentConfig = Field(default_factory=AgentConfig)
    agent_2: AgentConfig = Field(default_factory=AgentConfig)
    orchestrator: AgentConfig = Field(default_factory=AgentConfig)
    max_rounds: int = Field(default=20)
    max_time_minutes: int = Field(default=480)
    max_agents: int = Field(default=8)

    @classmethod
    def from_yaml(cls, path: Path) -> "AgentsConfig":
        """Load agent configuration from a YAML file."""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        agents_data = data.get("agents", data)
        return cls(
            agent_1=AgentConfig(**agents_data.get("agent_1", {})),
            agent_2=AgentConfig(**agents_data.get("agent_2", {})),
            orchestrator=AgentConfig(**agents_data.get("orchestrator", {})),
            max_rounds=data.get("max_rounds", 20),
            max_time_minutes=data.get("max_time_minutes", 480),
        )


# ------------------------------------------------------------------
# Global settings
# ------------------------------------------------------------------


class Settings(BaseSettings):
    """Global settings for Spec Editor.

    Loaded from environment variables (prefix SPEC_EDITOR__)
    and .env file.
    """

    model_config = SettingsConfigDict(
        env_prefix="SPEC_EDITOR__",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Logging
    log_level: str = Field(default="INFO")
    log_file: Path | None = Field(default=None)
    log_json: bool = Field(default=False)

    # Project paths
    project_path: Path | None = Field(default=None)
    methodology_path: Path | None = Field(default=None)
    agents_config_path: Path | None = Field(default=None)

    # Agent limits
    max_llm_calls: int = Field(default=30, description="Max LLM calls per run()")
    token_budget: int = Field(
        default=50000, description="Token budget before auto-compaction"
    )
    max_agents: int = Field(default=8, description="Max concurrent agents")

    # SRS template
    srs_template: Path = Field(
        default=Path("srs_template.yaml"), description="Path to SRS template"
    )

    # Dialogue timeouts
    max_time_minutes: int = Field(
        default=480, description="Max dialogue duration (min)"
    )

    # LLM timeouts
    llm_request_timeout: int = Field(
        default=90, description="Single request timeout (sec)"
    )
    llm_total_timeout: int = Field(
        default=90, description="Total timeout with retry (sec)"
    )

    # Blind voting
    adaptive_voting_strategy: bool = Field(
        default=False,
        description="Auto-select voting strategy by task context",
    )

    # Language
    prompt_language: str = Field(
        default="en",
        description="Language for agent prompts (en, ru, es, fr, de)",
    )


# ------------------------------------------------------------------
# Provider factory
# ------------------------------------------------------------------


def create_provider(config: AgentConfig):
    """Create an LLM provider from agent configuration."""
    from src.providers.litellm_provider import LiteLLMProvider

    return LiteLLMProvider(model=config.model)

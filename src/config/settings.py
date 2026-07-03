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
    role: str = Field(
        default="", description="Agent role: coding, tester, devops, etc."
    )
    skills: list[str] = Field(
        default_factory=list,
        description="Skill names loaded from skills/*.yaml",
    )


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
        """Load agent configuration from a YAML file, with env overrides."""
        import os

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        agents_data = data.get("agents", data)

        def _agent_from_data(key: str, prefix: str) -> AgentConfig:
            raw = agents_data.get(key, {})
            return AgentConfig(
                provider=os.environ.get(
                    f"{prefix}__PROVIDER", raw.get("provider", "openai")
                ),
                model=os.environ.get(f"{prefix}__MODEL", raw.get("model", "gpt-4o")),
                temperature=float(
                    os.environ.get(
                        f"{prefix}__TEMPERATURE",
                        raw.get("temperature", 0.7),
                    )
                ),
                max_tokens=int(
                    os.environ.get(
                        f"{prefix}__MAX_TOKENS",
                        raw.get("max_tokens", 4096),
                    )
                ),
            )

        return cls(
            agent_1=_agent_from_data("agent_1", "SPEC_EDITOR__AGENT_1"),
            agent_2=_agent_from_data("agent_2", "SPEC_EDITOR__AGENT_2"),
            orchestrator=_agent_from_data("orchestrator", "SPEC_EDITOR__ORCHESTRATOR"),
            max_rounds=data.get("max_rounds", 20),
            max_time_minutes=data.get("max_time_minutes", 480),
        )


# ------------------------------------------------------------------
# License configuration
# ------------------------------------------------------------------


class LicenseSettings(BaseModel):
    """License configuration for spec-editor monetization.

    Supports three backends:
    - ``noop`` (default): Always valid FREE tier — OSS version
    - ``gumroad``: Validates against GumRoad's /v2/licenses/verify API
    - ``file``: Offline validation via signed .license file

    Configured via ``local.yaml`` → ``license:`` section or
    environment variables with ``SPEC_EDITOR__LICENSE__`` prefix.
    """

    backend: str = Field(
        default="noop",
        description="License backend: noop | gumroad | file",
    )
    key: str = Field(
        default="",
        description="License key (GumRoad format: XXXX-XXXX-XXXX-XXXX)",
    )
    product_id: str = Field(
        default="",
        description="GumRoad product permalink ID for /v2/licenses/verify",
    )
    cloud_token_key: str = Field(
        default="",
        description="Separate key for cloud token proxy access",
    )
    cloud_proxy_url: str = Field(
        default="",
        description="Cloud token proxy base URL (e.g., https://proxy.example.com)",
    )
    cache_path: str = Field(
        default="~/.spec-editor/license.cache",
        description="Path to local license validation cache file",
    )
    cache_ttl_days: int = Field(
        default=7,
        description="Number of days to cache a successful validation",
    )
    offline_validation: bool = Field(
        default=False,
        description="If true, never hit the network — use cache or fail",
    )
    file_path: str = Field(
        default="",
        description="Path to .license file (for 'file' backend)",
    )
    public_key: str = Field(
        default="",
        description="Ed25519 public key for verifying signed .license files",
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
    trace_scenarios: str = Field(
        default="",
        description=(
            "Comma-separated scenario IDs to trace "
            "(e.g. SCN-002,SCN-003). Use * for all."
        ),
    )

    # Project paths
    project_path: Path | None = Field(default=None)
    methodology_path: Path | None = Field(default=None)
    agents_config_path: Path | None = Field(default=None)

    # Agent limits
    agent_implementation: str = Field(
        default="langgraph",
        description="Agent engine: 'langgraph' (default) or 'loop' (legacy)",
    )
    max_llm_calls: int = Field(default=30, description="Max LLM calls per run()")
    token_budget: int = Field(
        default=50000, description="Token budget before auto-compaction"
    )
    max_agents: int = Field(default=8, description="Max concurrent agents")

    # SRS template
    srs_template: Path = Field(
        default=Path("srs_template.yaml"), description="Path to SRS template"
    )

    # Source element protection
    restrict_source_deletion: bool = Field(
        default=True,
        description="Prevent deletion of SRC-* source elements",
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

    # License
    license: LicenseSettings = Field(
        default_factory=LicenseSettings,
        description="License configuration for Pro/Cloud tiers",
    )


# ------------------------------------------------------------------
# Provider factory
# ------------------------------------------------------------------


def create_provider(config: AgentConfig, settings: "Settings | None" = None):
    """Create an LLM provider from agent configuration.

    Uses the Secrets Provider for API key resolution.
    Falls back to environment variables if no secrets backend is configured.
    If a cloud proxy URL and cloud token are configured in license settings,
    routes through the Spec Editor Cloud Proxy for metered usage.
    """
    from src.providers.litellm_provider import LiteLLMProvider
    from src.secrets import create_secret_provider

    # Resolve API key via secrets backend
    secrets = create_secret_provider(Path.cwd())
    provider_upper = config.provider.upper()
    api_key = secrets.get_secret(f"{provider_upper}_API_KEY")

    # Cloud proxy configuration (for metered access)
    cloud_proxy_url = ""
    cloud_token = ""
    if settings is not None:
        cloud_proxy_url = settings.license.cloud_proxy_url
        cloud_token = settings.license.cloud_token_key or settings.license.key

    return LiteLLMProvider(
        model=config.model,
        api_key=api_key,
        cloud_proxy_url=cloud_proxy_url,
        cloud_token=cloud_token,
    )

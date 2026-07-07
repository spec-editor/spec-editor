"""Shared constants for spec-editor agents.

All hardcoded values that appear in multiple files live here.
Single source of truth — no duplication across agent modules.
"""

from pathlib import Path

# ──────────────────────────────────────────────────────────────────
# Version — single source of truth (reads VERSION file)
# ──────────────────────────────────────────────────────────────────

_VERSION_PATH = Path(__file__).resolve().parent.parent.parent / "VERSION"
try:
    __version__ = _VERSION_PATH.read_text().strip()
except (OSError, FileNotFoundError):
    __version__ = "0.1.0"

# ──────────────────────────────────────────────────────────────────
# Agent role identifiers
# ──────────────────────────────────────────────────────────────────

CODING = "coding"
TESTER = "tester"
PROJECT_MANAGER = "project-manager"
ANALYST_MANAGER = "analyst-manager"
DEVOPS = "devops"
REENGINEER = "reengineer"
REFACTOR = "refactor"

ALL_ROLES: tuple[str, ...] = (CODING, PROJECT_MANAGER, ANALYST_MANAGER, TESTER, DEVOPS, REFACTOR)
ALL_ROLES_WITH_REENGINEER: tuple[str, ...] = (*ALL_ROLES, REENGINEER)

# ──────────────────────────────────────────────────────────────────
# Agent-internal module identifiers (spec-editor pipeline itself)
# Meta-bugs referencing these modules are non-actionable for the
# coding agent — they loop forever.  The coding agent auto-deprecates
# them after max attempts.
# ──────────────────────────────────────────────────────────────────

AGENT_INTERNAL_MODULES: tuple[str, ...] = (
    "MOD-coding-agent",
    "MOD-pm-agent",
    "MOD-build",
    "MOD-analyst-manager-agent",
    "MOD-project-manager-agent",
    "MOD-tester-agent",
    "MOD-devops-agent",
    "MOD-reengineer-agent",
    "MOD-refactor-agent",
)


def is_agent_internal_bug(title: str = "", content: str = "") -> bool:
    """Return True if the bug references only spec-editor's own agent modules."""
    combined = f"{title} {content}"
    return any(m in combined for m in AGENT_INTERNAL_MODULES)


# ──────────────────────────────────────────────────────────────────
# Coding agent lifecycle limits
# ──────────────────────────────────────────────────────────────────

MAX_CODING_ATTEMPTS = 3          # attempts before blocking / auto-deprecating
TASK_MAX_LEN = 4000              # max task text sent to OpenCode
CONTENT_TRUNCATE = 2000          # truncate element content for task building
OUTPUT_TRUNCATE = 500            # truncate OpenCode output in failure notes

# ──────────────────────────────────────────────────────────────────
# Default LLM model (fallback when agents.yaml is unavailable)
# ──────────────────────────────────────────────────────────────────

DEFAULT_REASONING_MODEL = "deepseek/deepseek-reasoner"
DEFAULT_CHAT_MODEL = "deepseek/deepseek-chat"

# ──────────────────────────────────────────────────────────────────
# Environment variable names (set by VSCode extension or .env)
# ──────────────────────────────────────────────────────────────────

# Reasoning model — used by analyst, PM, coding agents for complex tasks
ENV_REASONING_MODEL = "SPEC_EDITOR__AGENT_1__MODEL"
ENV_REASONING_PROVIDER = "SPEC_EDITOR__AGENT_1__PROVIDER"
ENV_REASONING_TEMPERATURE = "SPEC_EDITOR__AGENT_1__TEMPERATURE"
ENV_REASONING_MAX_TOKENS = "SPEC_EDITOR__AGENT_1__MAX_TOKENS"

# Chat model — used by DevOps and for simpler generation tasks
ENV_CHAT_MODEL = "SPEC_EDITOR__AGENT_2__MODEL"
ENV_CHAT_PROVIDER = "SPEC_EDITOR__AGENT_2__PROVIDER"
ENV_CHAT_TEMPERATURE = "SPEC_EDITOR__AGENT_2__TEMPERATURE"
ENV_CHAT_MAX_TOKENS = "SPEC_EDITOR__AGENT_2__MAX_TOKENS"

# ──────────────────────────────────────────────────────────────────
# Queue names (Redis or file-based)
# ──────────────────────────────────────────────────────────────────

QUEUE_CODING = "coding"
QUEUE_TESTER = "tester"
QUEUE_PROJECT_MANAGER = "project-manager"
QUEUE_ANALYST_MANAGER = "analyst-manager"
QUEUE_DEVOPS = "devops"
QUEUE_REFACTOR = "refactor"

# ──────────────────────────────────────────────────────────────────
# Proactive scan intervals (seconds)
# ──────────────────────────────────────────────────────────────────

PROACTIVE_SCAN_INTERVAL = 60     # PM + AM scan interval
PROACTIVE_SCAN_START_DELAY = 10  # initial delay before first scan

# ──────────────────────────────────────────────────────────────────
# Infrastructure defaults
# ──────────────────────────────────────────────────────────────────

DEFAULT_REDIS_URL = "redis://localhost:6379"
DEFAULT_NATS_URL = "nats://localhost:4222"
DEFAULT_SMTP_HOST = "localhost"
DEFAULT_SMTP_FROM = "spec-editor@localhost"
DEFAULT_HTTP_API_URL = "http://localhost:8080"

# ──────────────────────────────────────────────────────────────────
# LLM provider defaults
# ──────────────────────────────────────────────────────────────────

DEFAULT_PROVIDER = "deepseek"
ALL_PROVIDERS: tuple[str, ...] = ("deepseek", "openai", "anthropic", "google", "groq", "ollama")

PROVIDER_ENV_VARS: dict[str, str] = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
}

# ──────────────────────────────────────────────────────────────────
# Agent config keys (used in agents.yaml serialisation)
# ──────────────────────────────────────────────────────────────────

AGENT_1 = "agent_1"
AGENT_2 = "agent_2"
ORCHESTRATOR = "orchestrator"
CONFIG_KEY_AGENTS = "agents"
CONFIG_KEY_MAX_ROUNDS = "max_rounds"
CONFIG_KEY_MAX_TIME_MINUTES = "max_time_minutes"

# ──────────────────────────────────────────────────────────────────
# Methodology
# ──────────────────────────────────────────────────────────────────

DEFAULT_METHODOLOGY = "waterfall"

# ──────────────────────────────────────────────────────────────────
# Aspect identifiers
# ──────────────────────────────────────────────────────────────────

ASPECT_MODULES = "modules"
ASPECT_USER_SCENARIOS = "user_scenarios"
ASPECT_USER_INTERFACE = "user_interface"
ASPECT_DATA_ENTITIES = "data_entities"
ASPECT_NON_FUNCTIONAL = "non_functional"
ASPECT_IMPLEMENTATION = "implementation"
ASPECT_METRICS = "metrics"
ASPECT_SOURCES = "sources"

ALL_ASPECTS: tuple[str, ...] = (
    ASPECT_MODULES,
    ASPECT_USER_SCENARIOS,
    ASPECT_USER_INTERFACE,
    ASPECT_DATA_ENTITIES,
    ASPECT_NON_FUNCTIONAL,
    ASPECT_IMPLEMENTATION,
    ASPECT_METRICS,
)

# ──────────────────────────────────────────────────────────────────
# Timeouts (seconds)
# ──────────────────────────────────────────────────────────────────

ARCH_CHECK_TIMEOUT = 60

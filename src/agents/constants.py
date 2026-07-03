"""Shared constants for spec-editor agents.

All hardcoded values that appear in multiple files live here.
Single source of truth — no duplication across agent modules.
"""

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

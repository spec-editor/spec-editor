"""Hardcoded team and agent registry for the VSCode Agents tree.

Three teams are currently defined (mapping to backend roles):
  - Product Team: Product Mgr, Analytic 1, Analytic 2
  - Project Team: Project Mgr, Coder, QA, DevOps
  - VSCode:       the VSCode chat-listening agent(s)

Teams are intentionally hardcoded for now. Making them data-driven
(teams.yaml / agents.yaml) is a separate, larger topic.

Every agent carries metadata used by the Agents tree and the
Settings/Chat panels:
  - role:   backend queue role (analyst-manager, coding, devops, ...)
  - skills: skill names loaded from skills.yaml / data/skills/*.yaml
  - aspects: aspects the agent focuses on (hardcoded; methodology's
    ``skills:`` list is currently flat and does not map skills→aspects)
  - system_prompt: resolved lazily via :func:`get_agent_details`
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# All methodology aspects (order matches methodology.yaml)
ALL_ASPECTS: list[str] = [
    "sources",
    "modules",
    "user_scenarios",
    "user_interface",
    "data_entities",
    "non_functional",
    "implementation",
    "metrics",
]

# ──────────────────────────────────────────────────────────────────
# Team definitions (hardcoded)
# ──────────────────────────────────────────────────────────────────

TEAMS: list[dict[str, Any]] = [
    {
        "key": "product",
        "name": "Product Team",
        "description": "Requirements analysis and specification creation.",
        "agents": ["product-mgr", "analytic-1", "analytic-2"],
    },
    {
        "key": "project",
        "name": "Project Team",
        "description": "Implementation, testing, and delivery.",
        "agents": ["project-mgr", "coder", "qa", "devops"],
    },
    {
        "key": "vscode",
        "name": "VSCode",
        "description": "Editor-integrated agents capturing requirements from chat.",
        "agents": ["vscode-copilot"],
    },
]

# ──────────────────────────────────────────────────────────────────
# Agent definitions (hardcoded)
# ──────────────────────────────────────────────────────────────────

AGENTS: dict[str, dict[str, Any]] = {
    # ── Product Team ──
    "product-mgr": {
        "name": "Product Mgr",
        "team": "product",
        "role": "analyst-manager",
        "skills": [],
        "aspects": ALL_ASPECTS,
        "description": "Coordinates analysts and reviews confirmed bugs against the spec.",
        "system_prompt_source": "",
    },
    "analytic-1": {
        "name": "Analytic 1",
        "team": "product",
        "role": "agent-1",
        "skills": ["system_analyst"],
        "aspects": ["sources", "modules", "user_scenarios", "user_interface",
                    "data_entities", "non_functional"],
        "description": "Primary analyst — creates specification elements from sources.",
        "system_prompt_source": "prompts:spec_agent",
    },
    "analytic-2": {
        "name": "Analytic 2",
        "team": "product",
        "role": "agent-2",
        "skills": ["cross_aspect_agent"],
        "aspects": ALL_ASPECTS,
        "description": "Cross-aspect linker — creates relationships between elements.",
        "system_prompt_source": "prompts:cross_aspect_agent",
    },
    # ── Project Team ──
    "project-mgr": {
        "name": "Project Mgr",
        "team": "project",
        "role": "project-manager",
        "skills": ["project_manager"],
        "aspects": ALL_ASPECTS,
        "description": "Coordinates the feedback loop: collect → analyse → ingest → update → code.",
        "system_prompt_source": "skill:project_manager",
    },
    "coder": {
        "name": "Coder",
        "team": "project",
        "role": "coding",
        "skills": ["coding_agent"],
        "aspects": ["implementation"],
        "description": "Implements requirements and fixes bugs.",
        "system_prompt_source": "skill:coding_agent",
    },
    "qa": {
        "name": "QA",
        "team": "project",
        "role": "tester",
        "skills": [],
        "aspects": ["implementation", "user_scenarios"],
        "description": "Tests implementation against requirements.",
        "system_prompt_source": "role:tester",
    },
    "devops": {
        "name": "DevOps",
        "team": "project",
        "role": "devops",
        "skills": ["devops"],
        "aspects": ["implementation", "non_functional"],
        "description": "Builds and deploys artifacts; diagnoses build errors.",
        "system_prompt_source": "skill:devops",
    },
    # ── VSCode Team ──
    "vscode-copilot": {
        "name": "VSCode",
        "team": "vscode",
        "role": "vscode-copilot",
        "skills": [],
        "aspects": ["sources"],
        "description": "Listens to Copilot chat and auto-captures requirements.",
        "system_prompt_source": "extension:chat-watcher",
    },
}


# ──────────────────────────────────────────────────────────────────
# Query helpers
# ──────────────────────────────────────────────────────────────────

def list_teams() -> list[dict[str, Any]]:
    """Return teams with their agent keys resolved to agent dicts."""
    result: list[dict[str, Any]] = []
    for team in TEAMS:
        entry = dict(team)
        entry["agents"] = [
            {**AGENTS[key], "key": key}
            for key in team["agents"]
            if key in AGENTS
        ]
        result.append(entry)
    return result


def list_agents() -> list[dict[str, Any]]:
    """Return a flat list of all agents (with team info)."""
    result: list[dict[str, Any]] = []
    for key, agent in AGENTS.items():
        entry = {**agent, "key": key}
        team = next((t for t in TEAMS if t["key"] == agent["team"]), None)
        entry["team_name"] = team["name"] if team else agent["team"]
        result.append(entry)
    return result


def get_agent(key: str) -> dict[str, Any] | None:
    """Return a single agent dict (with team name) or None."""
    agent = AGENTS.get(key)
    if agent is None:
        return None
    entry = {**agent, "key": key}
    team = next((t for t in TEAMS if t["key"] == agent["team"]), None)
    entry["team_name"] = team["name"] if team else agent["team"]
    return entry


# ──────────────────────────────────────────────────────────────────
# Detail resolvers (system prompt, skills, stats)
# ──────────────────────────────────────────────────────────────────

def _load_skill_prompts(project_path: str | Path) -> dict[str, str]:
    """Load skill prompts from all known skill sources.

    Uses SkillsRegistry to match the agent's own loading logic:
      - data/skills/ (individual skill files, e.g. coding.yaml, reengineer.yaml)
      - data/skills.yaml (flat list, shipped with the package)
      - <project>/skills.yaml + <project>/skills/ (project overrides)
    """
    from src.config.skills import SkillsRegistry

    root = Path(__file__).resolve().parent.parent.parent  # src/agents → src → root
    sources: list[Path] = []
    pkg_skills_dir = root / "data" / "skills"
    pkg_skills_file = root / "data" / "skills.yaml"
    proj_skills_dir = Path(project_path) / "skills"
    proj_skills_file = Path(project_path) / "skills.yaml"

    if pkg_skills_dir.is_dir():
        sources.append(pkg_skills_dir)
    if pkg_skills_file.exists():
        sources.append(pkg_skills_file)
    if proj_skills_dir.is_dir():
        sources.append(proj_skills_dir)
    if proj_skills_file.exists():
        sources.append(proj_skills_file)

    registry = SkillsRegistry(sources if sources else None)
    return {
        skill.name: skill.prompt
        for skill in registry.list()
    }


def _load_prompt_templates(project_path: str | Path) -> dict[str, str]:
    """Load spec_agent / cross_aspect_agent prompt templates from data/prompts/en.yaml."""
    import yaml

    root = Path(__file__).resolve().parent.parent.parent
    prompts_file = root / "data" / "prompts" / "en.yaml"
    if not prompts_file.exists():
        return {}
    try:
        data = yaml.safe_load(prompts_file.read_text()) or {}
        return {k: v for k, v in data.items() if isinstance(v, str)}
    except Exception:
        return {}


def _read_usage(project_path: str | Path, role: str) -> dict[str, Any]:
    """Read agent usage stats from tasks/{role}/usage.json (if present)."""
    import json

    usage_file = Path(project_path) / "tasks" / role / "usage.json"
    if not usage_file.exists():
        return {}
    try:
        data = json.loads(usage_file.read_text())
        return {
            "tokens_in": int(data.get("tokens_in", 0)),
            "tokens_out": int(data.get("tokens_out", 0)),
            "tasks_done": int(data.get("tasks_done", 0)),
            "started_at": data.get("started_at"),
            "updated_at": data.get("updated_at"),
        }
    except Exception:
        return {}


def get_agent_details(key: str, project_path: str | Path = "") -> dict[str, Any] | None:
    """Return full agent details for the Settings panel.

    Includes: skills (with prompts), aspects, system prompt (resolved),
    and usage stats (from tasks/{role}/usage.json — empty if absent).
    """
    agent = get_agent(key)
    if agent is None:
        return None

    skills = list(agent.get("skills", []))
    prompt_map = _load_skill_prompts(project_path) if project_path else {}
    prompt_templates = _load_prompt_templates(project_path) if project_path else {}

    # Resolve system prompt
    system_prompt = ""
    source = agent.get("system_prompt_source", "")
    if source.startswith("skill:"):
        skill_name = source.split(":", 1)[1]
        system_prompt = prompt_map.get(skill_name, "")
    elif source.startswith("prompts:"):
        tmpl_key = source.split(":", 1)[1]
        system_prompt = prompt_templates.get(tmpl_key, "")

    # Usage stats (from task queue usage.json)
    stats = _read_usage(project_path, agent["role"]) if project_path else {}

    return {
        **agent,
        "skills": [
            {
                "name": s,
                "prompt": prompt_map.get(s, ""),
                "loaded": s in prompt_map,
            }
            for s in skills
        ],
        "system_prompt": system_prompt,
        "stats": {
            "tasks_done": stats.get("tasks_done", 0),
            "tokens_in": stats.get("tokens_in", 0),
            "tokens_out": stats.get("tokens_out", 0),
            "llm_calls": stats.get("llm_calls", 0),
            "started_at": stats.get("started_at"),
            "updated_at": stats.get("updated_at"),
        },
    }

"""System MCP tool — teams, agents, tasks, questions, chat.

Consolidates the growing set of "system/UI" operations into ONE MCP tool
(``spec_editor_system``) with subcommands, so the main tool list stays small
and agents don't get confused by dozens of UI-oriented tools.

Subcommands:
  list_teams        — teams with their agents
  list_agents       — flat agent list
  get_agent         — agent details (skills, aspects, system prompt, stats)
  list_tasks        — incoming (queued) + outgoing (completed) tasks
  list_questions    — open questions from questions.jsonl
  chat              — send a message to an agent (LLM with agent context)
  set_agent_paused  — 2-state pause/resume toggle for an agent
  set_task_paused   — 2-state pause/resume toggle for a task
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from src.agents.teams import get_agent_details, list_agents, list_teams

# In-memory pause state, persisted to tasks/_pause_state.json so the
# VSCode extension can restart the MCP server without losing toggles.
_PAUSE_STATE_FILE = "_pause_state.json"
_pause_state: dict[str, bool] = {}
_pause_state_loaded: set[str] = set()  # project dirs already loaded


def _pause_file(project_path: str) -> Path:
    return Path(project_path) / "tasks" / _PAUSE_STATE_FILE


def _load_pause_state(project_path: str) -> None:
    """Load persisted pause state for a project (once per process)."""
    if project_path in _pause_state_loaded:
        return
    _pause_state_loaded.add(project_path)
    f = _pause_file(project_path)
    if not f.exists():
        return
    try:
        data = json.loads(f.read_text()) or {}
        for k, v in data.items():
            if isinstance(v, bool):
                _pause_state[k] = v
    except Exception:
        pass


def _save_pause_state(project_path: str) -> None:
    """Persist the in-memory pause state for a project."""
    f = _pause_file(project_path)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(_pause_state, indent=2))
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────
# Task queue readers
# ──────────────────────────────────────────────────────────────────

def _read_queue_tasks(project_path: str) -> dict[str, Any]:
    """Read incoming and outgoing tasks from the queue backend.

    Supports both Redis (local.yaml queue_url) and file-based fallback.
    Returns {"incoming": [...], "outgoing": [...], "backend": "redis"|"file"}.
    """
    from src.agents.events import get_queue_url

    queue_url = get_queue_url(project_path)
    if "redis" in queue_url:
        return _read_redis_tasks(queue_url)
    return _read_file_tasks(queue_url, project_path)


def _read_redis_tasks(queue_url: str) -> dict[str, Any]:
    """Read tasks from Redis streams (best-effort; empty on failure)."""
    incoming: list[dict[str, Any]] = []
    outgoing: list[dict[str, Any]] = []
    try:
        import redis as redis_lib

        r = redis_lib.from_url(queue_url, decode_responses=True,
                               socket_connect_timeout=2, socket_timeout=5)

        # Extract prefix from URL params (set by get_queue_url)
        prefix = ""
        if "prefix=" in queue_url:
            from urllib.parse import parse_qs, urlparse

            prefix = parse_qs(urlparse(queue_url).query).get("prefix", [""])[0]
            prefix = f"{prefix}:" if prefix else ""

        # Enumerate all role streams
        for role in ("coding", "tester", "project-manager", "analyst-manager",
                     "devops", "reengineer", "refactor"):
            task_stream = f"{prefix}tasks:{role}"
            done_stream = f"{prefix}done:{role}"

            # Incoming: recent queued tasks
            try:
                entries = r.xrevrange(task_stream, count=20)
                for msg_id, data in entries:
                    incoming.append({
                        "task_id": data.get("task_id", msg_id),
                        "role": role,
                        "payload": _summarize_payload(data.get("payload", "")),
                        "created_at": data.get("created_at", ""),
                        "stream_id": msg_id,
                    })
            except Exception:
                pass

            # Outgoing: recent completed tasks
            try:
                entries = r.xrevrange(done_stream, count=100)
                for msg_id, data in entries:
                    outgoing.append({
                        "task_id": data.get("task_id", msg_id),
                        "role": role,
                        "status": data.get("status", ""),
                        "result": data.get("result", "")[:200],
                    })
            except Exception:
                pass

        try:
            r.close()
        except Exception:
            pass

        # Sort: most recent first
        incoming.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    except Exception:
        pass
    return {"incoming": incoming, "outgoing": outgoing, "backend": "redis"}


def _read_file_tasks(queue_url: str, project_path: str) -> dict[str, Any]:
    """Read tasks from the file-based queue."""
    base = Path(queue_url.replace("file://", ""))
    if not base.is_absolute():
        base = Path(project_path) / base
    incoming: list[dict[str, Any]] = []
    outgoing: list[dict[str, Any]] = []

    for role_dir in base.iterdir() if base.is_dir() else []:
        if not role_dir.is_dir():
            continue
        role = role_dir.name
        pending = role_dir / "pending"
        done = role_dir / "done"

        if pending.is_dir():
            for f in sorted(pending.glob("*.json")):
                try:
                    data = json.loads(f.read_text()) or {}
                    incoming.append({
                        "task_id": f.stem,
                        "role": role,
                        "payload": _summarize_payload(json.dumps(data.get("payload", {}))),
                        "created_at": str(data.get("created_at", "")),
                    })
                except Exception:
                    continue

        if done.is_dir():
            for f in sorted(done.glob("*.json"))[-100:]:
                try:
                    data = json.loads(f.read_text()) or {}
                    outgoing.append({
                        "task_id": f.stem,
                        "role": role,
                        "status": data.get("status", ""),
                        "result": json.dumps(data.get("payload", {}))[:200],
                    })
                except Exception:
                    continue

    incoming.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    outgoing.sort(key=lambda t: t.get("task_id", ""), reverse=True)
    return {"incoming": incoming, "outgoing": outgoing, "backend": "file"}


def _summarize_payload(payload_str: str) -> str:
    """Extract a short summary from a task payload JSON string."""
    try:
        data = json.loads(payload_str)
    except Exception:
        return (payload_str or "")[:120]
    if isinstance(data, dict):
        for key in ("bug_id", "title", "task", "action", "instruction"):
            if key in data:
                val = str(data[key])
                return val[:120]
    return str(data)[:120]


# ──────────────────────────────────────────────────────────────────
# Question helpers
# ──────────────────────────────────────────────────────────────────

def _list_questions(project_path: str) -> list[dict[str, Any]]:
    """Read open questions from questions.jsonl."""
    from src.agents.questions import QuestionList

    ql = QuestionList(Path(project_path))
    return [
        {
            "id": q.id,
            "agent": q.agent,
            "question": q.question,
            "options": q.options,
            "status": q.status,
            "answer": q.answer,
            "timestamp": q.timestamp,
        }
        for q in ql.list_open()
    ]


# ──────────────────────────────────────────────────────────────────
# Chat
# ──────────────────────────────────────────────────────────────────

def _build_agent_context(agent_key: str, project_path: str) -> str:
    """Build current context for an agent chat: spec state + open questions."""
    from src.config.methodology import load_methodology

    parts: list[str] = []

    # Methodology summary
    method_path = Path(project_path) / "methodology.yaml"
    if method_path.exists():
        try:
            m = load_methodology(method_path)
            aspects_desc = ", ".join(a.name for a in m.aspects)
            parts.append(f"Methodology: {m.name} v{m.version}. Aspects: {aspects_desc}.")
        except Exception:
            pass

    # Element counts by aspect
    aspects_dir = Path(project_path) / "aspects"
    counts: dict[str, int] = {}
    if aspects_dir.is_dir():
        for md in aspects_dir.rglob("*.md"):
            if "_deleted" in md.parts:
                continue
            aspect = md.parent.name
            counts[aspect] = counts.get(aspect, 0) + 1
    if counts:
        parts.append("Current spec state (elements per aspect): " +
                     ", ".join(f"{a}:{c}" for a, c in sorted(counts.items())))

    # Open questions
    questions = _list_questions(project_path)
    if questions:
        q_desc = "; ".join(f"{q['id']} [{q['agent']}]: {q['question'][:80]}"
                           for q in questions[:10])
        parts.append(f"Open questions for the human: {q_desc}")

    return "\n".join(parts)


def _chat_with_agent(agent_key: str, message: str, project_path: str,
                     history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Send a message to an agent LLM with current context. Returns reply text."""
    from src.agents.tools import _get_provider_for_project
    from src.providers.base import Message, MessageRole

    agent = get_agent_details(agent_key, project_path)
    if agent is None:
        return {"status": "error", "message": f"Unknown agent '{agent_key}'"}

    system_prompt = agent.get("system_prompt", "")
    skill_prompts = "\n\n".join(
        f"## Skill: {s['name']}\n{s['prompt']}"
        for s in agent.get("skills", [])
        if s.get("prompt")
    )
    context = _build_agent_context(agent_key, project_path)

    system_text = (
        f"{system_prompt}\n\n{skill_prompts}\n\n"
        f"## Current context\n{context}\n\n"
        f"Answer the user's message concisely. If there are open questions for "
        f"the human, mention them and ask for clarification where needed."
    ).strip()

    messages = [Message(role=MessageRole.SYSTEM, content=system_text)]
    for h in (history or [])[-20:]:
        role = h.get("role", "user")
        content = h.get("content", "")
        if role == "user":
            messages.append(Message(role=MessageRole.USER, content=content))
        elif role == "assistant":
            messages.append(Message(role=MessageRole.ASSISTANT, content=content))
    messages.append(Message(role=MessageRole.USER, content=message))

    import asyncio

    async def _run():
        provider = _get_provider_for_project(project_path)
        return await provider.complete(messages=messages)

    try:
        response = asyncio.run(_run())
        return {
            "status": "ok",
            "reply": response.content or "",
            "reasoning": response.reasoning_content or "",
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


# ──────────────────────────────────────────────────────────────────
# Main dispatch
# ──────────────────────────────────────────────────────────────────

def handle_system_command(
    arguments: dict[str, Any],
    storage: Any = None,
) -> dict[str, Any]:
    """Handle a ``spec_editor_system`` subcommand. Returns content-ready dict."""
    command = arguments.get("command", "")
    project_path = arguments.get("project_path", "")

    # ── Stateless subcommands ──
    if command == "list_teams":
        return {"teams": list_teams()}

    if command == "list_agents":
        return {"agents": list_agents()}

    # ── Project-scoped subcommands ──
    if command == "get_agent":
        agent_key = arguments.get("agent", "")
        if not agent_key:
            return {"status": "error", "message": "agent is required"}
        details = get_agent_details(agent_key, project_path)
        return {"agent": details}

    if command == "list_tasks":
        tasks = _read_queue_tasks(project_path)
        return {"tasks": tasks}

    if command == "list_questions":
        return {"questions": _list_questions(project_path)}

    if command == "chat":
        agent_key = arguments.get("agent", "")
        message = arguments.get("message", "")
        if not agent_key or not message:
            return {"status": "error", "message": "agent and message are required"}
        history = arguments.get("history") or []
        return _chat_with_agent(agent_key, message, project_path, history)

    # ── Relocated internal tools ──
    if command == "capture_requirement":
        from src.agents.tools import capture_requirement_tool

        text = arguments.get("text", "")
        if not text:
            return {"status": "error", "message": "text is required"}
        if storage is None:
            return {"status": "error", "message": "storage unavailable (project not loaded)"}
        import asyncio

        return asyncio.run(
            capture_requirement_tool(
                storage, text, arguments.get("title", ""), project_path=project_path
            )
        )

    if command == "convert_source_file":
        from src.agents.tools_code import convert_source_file as _convert

        file_path = arguments.get("file_path", "")
        if not file_path:
            return {"status": "error", "message": "file_path is required"}
        import asyncio

        return asyncio.run(_convert(file_path))

    # ── Pause/resume toggles ──
    if command == "set_agent_paused":
        agent_key = arguments.get("agent", "")
        paused = bool(arguments.get("paused", False))
        if not agent_key:
            return {"status": "error", "message": "agent is required"}
        _load_pause_state(project_path)
        _pause_state[f"agent:{agent_key}"] = paused
        _save_pause_state(project_path)
        return {"status": "ok", "agent": agent_key, "paused": paused}

    if command == "set_task_paused":
        task_id = arguments.get("task_id", "")
        paused = bool(arguments.get("paused", False))
        if not task_id:
            return {"status": "error", "message": "task_id is required"}
        _load_pause_state(project_path)
        _pause_state[f"task:{task_id}"] = paused
        _save_pause_state(project_path)
        return {"status": "ok", "task_id": task_id, "paused": paused}

    return {"status": "error", "message": f"Unknown command '{command}'"}


def get_pause_state(project_path: str) -> dict[str, bool]:
    """Return the full pause state (for decorating list_tasks/list_agents)."""
    _load_pause_state(project_path)
    return dict(_pause_state)


# ──────────────────────────────────────────────────────────────────
# Schema
# ──────────────────────────────────────────────────────────────────

def spec_editor_system_schema() -> dict[str, Any]:
    """JSON Schema for the consolidated spec_editor_system tool."""
    return {
        "name": "spec_editor_system",
        "description": (
            "[System] Teams, agents, tasks, questions, chat, capture, convert, "
            "and pause toggles. Use `command` to select the operation. Commands: "
            "list_teams, list_agents, get_agent, list_tasks, list_questions, "
            "chat, capture_requirement, convert_source_file, set_agent_paused, "
            "set_task_paused."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Operation to perform (see tool description).",
                },
                "project_path": {
                    "type": "string",
                    "description": "Path to the spec-editor project (optional for stateless commands).",
                },
                "agent": {
                    "type": "string",
                    "description": "Agent key (for get_agent, chat, set_agent_paused).",
                },
                "message": {
                    "type": "string",
                    "description": "Chat message (for chat).",
                },
                "history": {
                    "type": "array",
                    "description": "Optional chat history: [{role: user|assistant, content: ...}].",
                },
                "text": {
                    "type": "string",
                    "description": "Requirement text (for capture_requirement).",
                },
                "title": {
                    "type": "string",
                    "description": "Short title (for capture_requirement, optional).",
                },
                "file_path": {
                    "type": "string",
                    "description": "File path (for convert_source_file).",
                },
                "task_id": {
                    "type": "string",
                    "description": "Task ID (for set_task_paused).",
                },
                "paused": {
                    "type": "boolean",
                    "description": "Pause state (for set_agent_paused / set_task_paused).",
                },
            },
            "required": ["command"],
        },
    }

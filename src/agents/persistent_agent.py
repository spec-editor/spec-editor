"""Persistent Agent Worker — listens on task queue, executes, reports.

Usage::

    spec-editor agent coding --watch     # listens on tasks:coding
    spec-editor agent tester --watch     # listens on tasks:tester

Or from code::

    from src.agents.persistent_agent import AgentWorker
    worker = AgentWorker(role="coding", project_path=".")
    await worker.run()
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from src.agents.agent import Agent
from src.agents.task_queue import Task, TaskResult
from src.agents.constants import (
    AGENT_INTERNAL_MODULES,
    ANALYST_MANAGER,
    CODING,
    DEFAULT_CHAT_MODEL,
    DEFAULT_REASONING_MODEL,
    DEVOPS,
    ENV_CHAT_MODEL,
    ENV_CHAT_PROVIDER,
    ENV_CHAT_TEMPERATURE,
    ENV_CHAT_MAX_TOKENS,
    ENV_REASONING_MODEL,
    ENV_REASONING_PROVIDER,
    ENV_REASONING_TEMPERATURE,
    ENV_REASONING_MAX_TOKENS,
    MAX_CODING_ATTEMPTS,
    PROJECT_MANAGER,
    QUEUE_CODING,
    QUEUE_PROJECT_MANAGER,
    QUEUE_REFACTOR,
    QUEUE_TESTER,
    REENGINEER,
    REFACTOR,
    TASK_MAX_LEN,
    TESTER,
    is_agent_internal_bug,
)


class AgentWorker(Agent):
    """Persistent agent. Inherits from Agent for skills, logging, proactive scan.

    Args:
        role: ``"coding"``, ``"tester"``, ``"project-manager"``, ``"devops"``.
        project_path: Path to the spec-editor project.
        queue_url: Connection URL. Auto-detected if empty.
    """

    def __init__(
        self,
        role: str,
        project_path: str | Path,
        queue_url: str = "",
    ) -> None:
        # ── Load LLM provider for roles that need it ──
        provider = None
        tools_list = None
        tool_handlers = None
        system_prompt = ""
        skills_to_load: list[str] = []

        if role == ANALYST_MANAGER or role == REENGINEER:
            from src.providers.base import ToolDef
            provider = self._make_reasoning_provider()
            tools_list = [
                ToolDef(name="read_element", description="Read an element by ID", parameters={
                    "type": "object", "properties": {
                        "element_id": {"type": "string"}
                    }, "required": ["element_id"]
                }),
                ToolDef(name="write_element", description="Create or update an element. For new elements, provide aspect, element_type, title. For existing, just id + fields to update.", parameters={
                    "type": "object", "properties": {
                        "id": {"type": "string"},
                        "status": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "content": {"type": "string"},
                        "aspect": {"type": "string", "description": "Required for NEW elements"},
                        "element_type": {"type": "string", "description": "Required for NEW elements"},
                        "title": {"type": "string", "description": "Title for NEW elements"},
                        "parent": {"type": "string", "description": "Parent element ID (optional)"},
                    }, "required": ["id"]
                }),
                ToolDef(name="list_all_elements", description="List all elements", parameters={
                    "type": "object", "properties": {}
                }),
            ]
            tool_handlers = {
                "read_element": self._tool_read_element,
                "write_element": self._tool_write_element,
                "list_all_elements": self._tool_list_all_elements,
            }
            # Reengineer gets additional code-analysis tools
            if role == REENGINEER:
                tools_list.extend([
                    ToolDef(name="get_file_tree", description="List the project file structure", parameters={
                        "type": "object", "properties": {
                            "subdir": {"type": "string", "description": "Subdirectory path (default: project root)"}
                        }, "required": []
                    }),
                    ToolDef(name="search_code", description="Search for a pattern in code files", parameters={
                        "type": "object", "properties": {
                            "pattern": {"type": "string", "description": "Text or regex pattern to search for"},
                            "path": {"type": "string", "description": "Subdirectory to search (default: project root)"}
                        }, "required": ["pattern"]
                    }),
                    ToolDef(name="add_relationship", description="Add a relationship between elements", parameters={
                        "type": "object", "properties": {
                            "source_id": {"type": "string"},
                            "rel_type": {"type": "string"},
                            "target_id": {"type": "string"}
                        }, "required": ["source_id", "rel_type", "target_id"]
                    }),
                    ToolDef(name="search_elements", description="Search elements by text query", parameters={
                        "type": "object", "properties": {
                            "query": {"type": "string"}
                        }, "required": ["query"]
                    }),
                    ToolDef(name="list_aspect", description="List elements in an aspect", parameters={
                        "type": "object", "properties": {
                            "aspect_name": {"type": "string"}
                        }, "required": ["aspect_name"]
                    }),
                    ToolDef(name="read_file", description="Read a code file content", parameters={
                        "type": "object", "properties": {
                            "file_path": {"type": "string", "description": "Relative path from project root"}
                        }, "required": ["file_path"]
                    }),
                ])
                tool_handlers.update({
                    "get_file_tree": self._tool_get_file_tree,
                    "search_code": self._tool_search_code,
                    "add_relationship": self._tool_add_relationship,
                    "search_elements": self._tool_search_elements,
                    "list_aspect": self._tool_list_aspect,
                    "read_file": self._tool_read_file,
                })
            skills_to_load = [role]

        super().__init__(
            name=f"Agent {role}",
            role=role,
            project_path=project_path,
            queue_url=queue_url,
            provider=provider,
            tools=tools_list or [],
            tool_handlers=tool_handlers or {},
            system_prompt=system_prompt,
            skills=skills_to_load,
        )

    async def run(self) -> None:
        """Connect to queue and process tasks forever."""
        await self.run_queue()

    async def handle_task(self, task: Task) -> TaskResult:
        """Dispatch to role-specific handler."""
        handler = getattr(self, f"_handle_{self.role.replace('-', '_')}", None)
        if handler is None:
            return TaskResult(
                task_id=task.task_id,
                role=self.role,
                status="failed",
                payload={"error": f"No handler for role: {self.role}"},
            )
        try:
            return await handler(task)
        except Exception as exc:
            self._log.error("task_failed", task_id=task.task_id, error=str(exc))
            return TaskResult(
                task_id=task.task_id,
                role=self.role,
                status="failed",
                payload={"error": str(exc)},
            )

    # ── LLM provider factory (analyst-manager) ──

    @staticmethod
    def _read_model_from_config(agent_key: str, env_model: str, default_model: str) -> str:
        """Resolve model name with fallback chain.

        Priority (highest first):
        1. VSCode extension env var (SPEC_EDITOR__AGENT_1__MODEL / AGENT_2__MODEL)
        2. .env file (loaded by dotenv into os.environ)
        3. agents.yaml in project root (synced from VSCode settings)
        4. Hardcoded default constant
        """
        import os
        from pathlib import Path

        # 1. VSCode extension sets these env vars when spawning the process
        model = os.environ.get(env_model, "")
        if model:
            return model

        # 2. .env is already loaded by main.py via load_dotenv() — check again
        #    (some env vars might use a slightly different naming convention)
        #    Already covered by os.environ.get above.

        # 3. agents.yaml in project root (synced from VSCode settings)
        try:
            import yaml
            agents_yaml = Path(os.environ.get(
                "SPEC_EDITOR_AGENTS",
                str(Path.cwd() / "agents.yaml")
            ))
            if agents_yaml.exists():
                config = yaml.safe_load(agents_yaml.read_text())
                model = config.get("agents", {}).get(agent_key, {}).get(
                    "model", ""
                )
                if model:
                    return model
        except Exception:
            pass

        # 4. Fallback to hardcoded default
        return default_model

    @staticmethod
    def _make_reasoning_provider():
        """Create the Reasoning LLM provider (Agent 1).

        Used by: analyst-manager, reengineer, project-manager, coding agents.
        Reads SPEC_EDITOR__AGENT_1__MODEL → .env → agents.yaml → default.

        Returns a LiteLLMProvider configured for complex reasoning tasks.
        """
        import os
        from src.providers.litellm_provider import LiteLLMProvider

        model = AgentWorker._read_model_from_config(
            agent_key="agent_1",
            env_model=ENV_REASONING_MODEL,
            default_model=DEFAULT_REASONING_MODEL,
        )
        return LiteLLMProvider(model=model)

    @staticmethod
    def _make_chat_provider():
        """Create the Chat LLM provider (Agent 2).

        Used by: devops agent, simpler generation tasks.
        Reads SPEC_EDITOR__AGENT_2__MODEL → .env → agents.yaml → default.

        Returns a LiteLLMProvider configured for faster chat-style tasks.
        """
        import os
        from src.providers.litellm_provider import LiteLLMProvider

        model = AgentWorker._read_model_from_config(
            agent_key="agent_2",
            env_model=ENV_CHAT_MODEL,
            default_model=DEFAULT_CHAT_MODEL,
        )
        return LiteLLMProvider(model=model)

    async def _tool_read_element(self, element_id: str) -> dict:
        """Tool: read an element by ID."""
        from src.storage.filesystem import FilesystemStorage
        storage = FilesystemStorage(self._project_path)
        try:
            el = storage.read_element(element_id)
        except (KeyError, FileNotFoundError) as exc:
            return {"error": f"Element not found: {element_id}", "id": element_id}
        except Exception as exc:
            return {"error": str(exc), "id": element_id}
        return {"id": el.id, "title": el.title, "status": el.status.value,
                "tags": el.tags, "content": el.content or ""}

    async def _tool_write_element(self, id: str = "", status: str = "", tags: list | None = None,
                                   content: str = "", aspect: str = "",
                                   element_type: str = "", title: str = "",
                                   parent: str = "", element_id: str = "") -> dict:
        """Tool: CREATE or UPDATE an element. Delegates to shared write_element_tool.

        Uses the same implementation as the MCP server — methodology validation,
        parent hierarchy checks, cycle detection, and parent→children auto-sync.
        """
        # Accept both 'id' and 'element_id' (LLMs use both names)
        resolved_id = id or element_id
        if not resolved_id:
            return {"error": "Either 'id' or 'element_id' is required"}

        from pathlib import Path as _Path
        from src.agents.tools import write_element_tool
        from src.config.methodology import load_methodology
        from src.storage.filesystem import FilesystemStorage

        storage = FilesystemStorage(self._project_path)
        method_path = _Path(self._project_path) / "methodology.yaml"
        try:
            methodology = load_methodology(method_path)
        except Exception:
            methodology = None

        # Build args matching write_element_tool signature
        result = await write_element_tool(
            storage=storage,
            methodology=methodology,
            id=resolved_id,
            aspect=aspect,
            element_type=element_type,
            title=title or resolved_id,
            content=content or "",
            parent=parent or None,
            tags=tags or [],
            status=status or "confirmed",
        )

        if result.get("status") == "error":
            return {"error": result.get("message", "Write failed")}

        self._log.info("am_tool_write", element_id=resolved_id, status=status,
                      content_len=len(content))
        print(f"  [progress] WRITE {resolved_id}: {title or resolved_id} ({result.get('status', 'ok')})")
        return {"ok": True, "id": result.get("element_id", resolved_id),
                "content_len": len(content)}

    async def _tool_list_all_elements(self) -> dict:
        """Tool: list all elements."""
        from src.storage.filesystem import FilesystemStorage
        storage = FilesystemStorage(self._project_path)
        elements = []
        for s in storage.list_all():
            elements.append({"id": s.id, "title": s.title, "status": s.status.value, "aspect": s.aspect})
        return {"elements": elements}

    # ── Reengineer tools ──

    async def _tool_get_file_tree(self, subdir: str = "") -> dict:
        """Tool: list project file structure."""
        import os
        root = self._project_path
        if subdir:
            root = root / subdir
        if not root.exists():
            return {"error": f"Directory not found: {subdir}", "files": []}

        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            # Skip common non-code directories
            dirnames[:] = [d for d in dirnames if d not in (
                ".git", "__pycache__", "node_modules", ".venv", "dist",
                "build", ".next", ".vscode-test", "egg-info"
            )]
            rel = os.path.relpath(dirpath, self._project_path)
            for fn in filenames:
                if fn.startswith(".") and fn != ".env":
                    continue
                files.append(os.path.join(rel, fn) if rel != "." else fn)

        total = len(files)
        self._log.info("reengineer_file_tree", root=str(root), count=total)
        print(f"  [progress] File tree scanned: {total} files found")
        return {"root": str(root), "count": total, "files": sorted(files)[:500]}

    async def _tool_search_code(self, pattern: str, path: str = "") -> dict:
        """Tool: search for pattern in code files."""
        import subprocess
        root = self._project_path
        if path:
            root = root / path
        try:
            result = subprocess.run(
                ["grep", "-rn", "--include=*.py", "--include=*.ts", "--include=*.tsx",
                 "--include=*.js", "--include=*.yaml", "--include=*.json",
                 "--include=*.md", "--include=Makefile", "--include=Dockerfile",
                 pattern, str(root)],
                capture_output=True, text=True, timeout=15
            )
            lines = result.stdout.strip().split("\n")[:100] if result.stdout else []
            self._log.info("reengineer_search_code", pattern=pattern, matches=len(lines))
            print(f"  [progress] search_code('{pattern[:50]}'): {len(lines)} matches")
            return {"pattern": pattern, "matches": len(lines), "lines": lines}
        except Exception as exc:
            return {"pattern": pattern, "error": str(exc), "lines": []}

    # ── Valid relationship types (from methodology.yaml) ──
    _VALID_REL_TYPES: set[str] = {
        "consists_of", "depends_on", "refines", "next_step",
        "interacts_with", "navigates_to", "references", "applies_to",
        "implements", "implemented_by", "tested_by",
        "measures", "contains", "triggers_on",
    }

    async def _tool_add_relationship(self, source_id: str, rel_type: str,
                                      target_id: str) -> dict:
        """Tool: add a relationship between two elements.

        Rejects unknown relationship types. Use the 'parent' field on
        write_element for parent/child hierarchy — NOT a relationship.
        """
        # Block common LLM inventions
        if rel_type in ("parent", "child", "part_of", "belongs_to", "is_a",
                        "has_a", "related_to", "associated_with"):
            return {"error": (
                f"'{rel_type}' is not a valid relationship type. "
                f"Use one of: {', '.join(sorted(self._VALID_REL_TYPES))}. "
                f"For parent/child hierarchy, use write_element with parent='TARGET_ID'."
            )}
        if rel_type not in self._VALID_REL_TYPES:
            return {"error": (
                f"Unknown relationship type '{rel_type}'. "
                f"Valid types: {', '.join(sorted(self._VALID_REL_TYPES))}"
            )}
        from src.storage.filesystem import FilesystemStorage
        from src.storage.models import RelationshipEntry
        storage = FilesystemStorage(self._project_path)
        el = storage.read_element(source_id)
        if rel_type not in el.relationships:
            el.relationships[rel_type] = []
        already = [e for e in el.relationships[rel_type] if e.target == target_id]
        if not already:
            el.relationships[rel_type].append(
                RelationshipEntry(role=rel_type, target=target_id)
            )
            storage.write_element(el)
            self._log.info("reengineer_add_rel", src=source_id, rel=rel_type, tgt=target_id)
            return {"ok": True, "source": source_id, "relationship": rel_type, "target": target_id}
        return {"ok": True, "skipped": "already exists"}

    async def _tool_search_elements(self, query: str) -> dict:
        """Tool: search elements by text query."""
        from src.storage.filesystem import FilesystemStorage
        storage = FilesystemStorage(self._project_path)
        results = []
        q_lower = query.lower()
        for summary in storage.list_all():
            if q_lower in summary.id.lower() or q_lower in summary.title.lower():
                results.append({
                    "id": summary.id, "title": summary.title,
                    "status": summary.status.value, "aspect": summary.aspect
                })
        # Also search in content for deeper matches
        if len(results) < 5:
            for summary in storage.list_all():
                if summary.id in {r["id"] for r in results}:
                    continue
                try:
                    el = storage.read_element(summary.id)
                    if el.content and q_lower in el.content.lower():
                        results.append({
                            "id": el.id, "title": el.title,
                            "status": el.status.value, "aspect": el.aspect
                        })
                except Exception:
                    pass
        self._log.info("reengineer_search_elements", query=query, found=len(results))
        return {"query": query, "found": len(results), "results": results[:20]}

    async def _tool_list_aspect(self, aspect_name: str) -> dict:
        """Tool: list all elements in an aspect."""
        from src.storage.filesystem import FilesystemStorage
        storage = FilesystemStorage(self._project_path)
        elements = []
        for summary in storage.list_all():
            if summary.aspect == aspect_name:
                elements.append({
                    "id": summary.id, "title": summary.title,
                    "status": summary.status.value, "element_type": summary.element_type
                })
        return {"aspect": aspect_name, "count": len(elements), "elements": elements}

    async def _tool_read_file(self, file_path: str, offset: int = 0,
                               limit: int | None = None) -> dict:
        """Tool: read a code file's content, optionally with offset/limit."""
        full_path = self._project_path / file_path
        if not full_path.exists():
            return {"error": f"File not found: {file_path}", "content": ""}
        try:
            content = full_path.read_text(encoding="utf-8")
            total_len = len(content)
            if offset or limit:
                lines = content.split("\n")
                if limit:
                    lines = lines[offset:offset + limit]
                else:
                    lines = lines[offset:]
                content = "\n".join(lines)
            self._log.info("reengineer_read_file", path=file_path, size=total_len)
            return {"file": file_path, "size": total_len, "content": content[:5000]}
        except Exception as exc:
            return {"error": str(exc), "file": file_path, "content": ""}

    # ── Redis helpers (overridable for testing) ──

    async def _push_to_coding_queue(self, payload: dict) -> None:
        """Push a task to the coding agent Redis queue.  Override in tests."""
        from src.agents.task_queue import AbstractTaskQueue, get_queue_url

        queue_url = get_queue_url(self._project_path)
        q = AbstractTaskQueue.connect(queue_url)
        await q.connect()
        await q.push(QUEUE_CODING, payload)
        await q.close()

    async def _push_to_pm_queue(self, payload: dict) -> None:
        """Push a task to the project-manager Redis queue.  Override in tests."""
        from src.agents.task_queue import AbstractTaskQueue, get_queue_url

        queue_url = get_queue_url(self._project_path)
        q = AbstractTaskQueue.connect(queue_url)
        await q.connect()
        await q.push(QUEUE_PROJECT_MANAGER, payload)
        await q.close()

    # ── Coding ──

    async def _handle_coding(self, task: Task) -> TaskResult:
        """Handle a coding task with full lifecycle management.

        Flow:
        1. Run OpenCode to fix the bug / implement the element
        2. Run targeted tests (pytest on derived leaf's test file)
        3. Tests pass → set element status=confirmed, return success
        4. Tests fail → increment attempt counter
           - attempts < 3 → re-push to Redis for retry
           - attempts >= 3 → set status=blocked, push to PM for refinement
        """
        from pathlib import Path as _Path

        from spec_editor_cycle.providers import get_provider

        from src.agents.task_queue import AbstractTaskQueue, get_queue_url
        from src.storage.filesystem import FilesystemStorage
        from src.storage.models import ElementStatus

        storage = FilesystemStorage(self._project_path)
        bug_id = task.payload.get("bug_id", "") or task.payload.get("element_id", "")
        task_text = task.payload.get("task", "")
        action = task.payload.get("action", "fix")

        # Build task description from element if not provided
        if not task_text and bug_id:
            try:
                el = storage.read_element(bug_id)
                title = getattr(el, "title", "") or ""
                content = (getattr(el, "content", "") or "")[:2000]
                task_text = f"{action} {bug_id}: {title}\n\n{content}"
            except Exception:
                task_text = f"{action} {bug_id}"
        model = task.payload.get("model", DEFAULT_REASONING_MODEL)
        leaf_id = task.payload.get("leaf_id", "")
        attempt = int(task.payload.get("attempt", 1))

        self._log.info(
            "coding_start",
            bug_id=bug_id,
            attempt=attempt,
            leaf_id=leaf_id,
        )

        # ── Atomically claim the bug: clear 'dispatched', set 'attempts:N' ──
        try:
            bug = storage.read_element(bug_id)
            bug.tags = [t for t in (bug.tags or []) if t != "dispatched"]
            bug.tags = [t for t in bug.tags if not t.startswith("attempts:")]
            bug.tags.append(f"attempts:{attempt}")
            storage.write_element(bug)
        except Exception:
            pass  # best-effort — element write is not critical here

        # ── 1. Run OpenCode ──
        provider = get_provider("opencode", str(self._project_path))
        provider.shutdown()  # fresh session per bug
        result = await provider.run(
            storage=storage,
            task=task_text[:TASK_MAX_LEN],
            model=model,
        )

        if result.get("status") != "ok":
            self._log.warning("coding_agent_failed", bug_id=bug_id, status=result.get("status"))
            # Build informative failure detail from OpenCode output
            err_detail = result.get("errors", "") or result.get("error", "")
            out_detail = result.get("output", "")[-500:]
            detail_parts = []
            if err_detail:
                detail_parts.append(f"stderr: {err_detail[:500]}")
            if out_detail:
                detail_parts.append(f"output: {out_detail[:500]}")
            detail = "\n".join(detail_parts) if detail_parts else result.get("status", "unknown")
            failure_note = self._build_failure_note(
                attempt, "opencode", detail
            )
            return await self._handle_coding_failure(
                storage, bug_id, leaf_id, task_text, model, attempt, task.task_id,
                failure_note=failure_note,
            )

        # ── 2. Run targeted tests ──
        from src.agents.test_utils import run_pytest

        tests_pass = False
        test_output = ""
        test_file = self._find_test_file(leaf_id) if leaf_id else None
        if test_file and test_file.exists():
            tests_pass, pytest_output = run_pytest(test_file, str(self._project_path))
            if not tests_pass:
                test_output = (
                    f"Tests in {test_file.name} FAILED:\n"
                    f"```\n{pytest_output}\n```"
                )
            self._log.info(
                "coding_tests",
                bug_id=bug_id,
                passed=tests_pass,
                test_file=str(test_file),
            )
        else:
            tests_pass = bool(result.get("files_changed"))

        # ── 3. Update element status ──
        if tests_pass:
            try:
                bug = storage.read_element(bug_id)
                bug.status = ElementStatus.CONFIRMED
                # Clear attempt tags
                bug.tags = [
                    t for t in bug.tags
                    if not t.startswith("attempts:") and t not in ("blocked", "dispatched")
                ]
                storage.write_element(bug)
                self._log.info("coding_confirmed", bug_id=bug_id)

                # ── Trigger QA: push acceptance test task to tester queue ──
                try:
                    from src.agents.task_queue import AbstractTaskQueue, get_queue_url
                    q_url = get_queue_url(str(self._project_path))
                    q = AbstractTaskQueue.connect(q_url)
                    await q.connect()
                    await q.push(QUEUE_TESTER, {
                        "action": "acceptance_test",
                        "triggered_by": bug_id,
                        "max_leaves": 5,
                    })
                    await q.close()
                except Exception as qe:
                    self._log.debug("qa_trigger_failed", error=str(qe))

                return TaskResult(
                    task_id=task.task_id,
                    role="coding",
                    status="ok",
                    payload={
                        "bug_id": bug_id,
                        "action": "confirmed",
                        "files_changed": result.get("files_changed", []),
                    },
                )
            except Exception as exc:
                self._log.error("coding_confirm_failed", bug_id=bug_id, error=str(exc))

        # ── 4. Tests failed — retry or block ──
        failure_note = self._build_failure_note(
            attempt, "tests",
            test_output[:500] if test_output else "Tests failed"
        )
        return await self._handle_coding_failure(
            storage, bug_id, leaf_id, task_text, model, attempt, task.task_id,
            failure_note=failure_note,
        )

    async def _handle_coding_failure(
        self,
        storage: Any,
        bug_id: str,
        leaf_id: str,
        task_text: str,
        model: str,
        attempt: int,
        task_id: str,
        failure_note: str = "",
    ) -> TaskResult:
        """Retry or block a bug after coding/test failure. Shared by both paths.

        If ``failure_note`` is provided, it is appended to the task text so
        the next attempt has context about what went wrong.
        """
        from src.storage.models import ElementStatus

        # ── Append failure context to task text ONLY, NOT to element content ──
        # Element content stays clean — failure details go in the task for next attempt.
        # Strip old failure notes to prevent unbounded growth, keep only latest
        import re as _re
        clean_task = _re.sub(r'\n?---\n## Previous attempt.*?(?=\n?---\n## Previous attempt|\Z)', '', task_text, flags=_re.DOTALL)
        clean_task = _re.sub(r'\n?---\n$', '', clean_task).strip()
        enriched_task = clean_task
        if failure_note:
            # Keep base task under 2000 chars so failure note fits
            if len(clean_task) > 2000:
                clean_task = clean_task[:1800] + "\n...(truncated)\n"
            enriched_task = clean_task + failure_note
            # Hard cap at 4000
            if len(enriched_task) > 4000:
                enriched_task = enriched_task[-4000:]

        try:
            bug = storage.read_element(bug_id)
            bug.tags = [t for t in bug.tags if not t.startswith("attempts:")]
            bug.tags.append(f"attempts:{attempt}")
            storage.write_element(bug)

            if attempt >= MAX_CODING_ATTEMPTS:
                # ── Auto-deprecate meta-bugs (pipeline-internal) ──
                # Bugs referencing spec-editor agent modules have no actionable
                # project fix. They loop forever if we keep retrying.
                content = getattr(bug, "content", "") or ""
                title = getattr(bug, "title", "") or ""
                if is_agent_internal_bug(title=title, content=content):
                    bug.status = ElementStatus.DEPRECATED
                    bug.tags = [t for t in bug.tags if not t.startswith("attempts:")]
                    bug.tags.append("meta_bug_deprecated")
                    storage.write_element(bug)
                    self._log.info("coding_auto_deprecated", bug_id=bug_id, attempt=attempt,
                                   reason="meta-bug (pipeline-internal)")
                    return TaskResult(task_id=task_id, role=CODING, status="ok",
                        payload={"bug_id": bug_id, "attempt": attempt, "action": "auto_deprecated"})

                bug.status = ElementStatus.BLOCKED
                bug.tags.append("blocked")
                storage.write_element(bug)
                self._log.info("coding_blocked", bug_id=bug_id, attempt=attempt)
                await self._push_to_pm_queue({
                    "bug_id": bug_id, "leaf_id": leaf_id,
                    "action": "refine_blocked",
                    "bug_title": bug.title,
                    "bug_content": bug.content or "",
                    "issue": f"Failed {attempt} coding attempts",
                })
                return TaskResult(task_id=task_id, role=CODING, status="blocked",
                    payload={"bug_id": bug_id, "attempt": attempt, "action": "blocked"})
            else:
                self._log.info("coding_retry", bug_id=bug_id, attempt=attempt)
                await self._push_to_coding_queue({
                    "bug_id": bug_id, "leaf_id": leaf_id,
                    "task": enriched_task, "model": model,
                    "attempt": attempt + 1,
                })
                return TaskResult(task_id=task_id, role=CODING, status="retry",
                    payload={"bug_id": bug_id, "attempt": attempt + 1, "action": "retry"})
        except Exception as exc:
            self._log.error("coding_status_update_failed", bug_id=bug_id, error=str(exc))
            return TaskResult(task_id=task_id, role=CODING, status="failed",
                payload={"bug_id": bug_id, "error": str(exc)})

    # ── Refactor ──

    async def _handle_refactor(self, task: Task) -> TaskResult:
        """Handle a refactoring task.

        Flow:
        1. Load refactor skill prompt from skills/refactor.yaml
        2. Run baseline tests to establish safety net
        3. Dispatch to OpenCode with refactor-specific task + skill prompt
        4. Run tests after refactoring — MUST stay green
        5. Report results (files changed, smells fixed, tests passing)

        Unlike the coding agent, the refactor agent:
        - Does NOT track bug lifecycle (no element status updates)
        - Does NOT retry on failure (reverts and reports instead)
        - MUST preserve existing test behaviour
        """
        from pathlib import Path as _Path

        from spec_editor_cycle.providers import get_provider

        from src.storage.filesystem import FilesystemStorage

        storage = FilesystemStorage(self._project_path)
        target_files = task.payload.get("target_files", [])
        target_dirs = task.payload.get("target_dirs", ["src/"])
        refactor_type = task.payload.get("refactor_type", "general")
        task_description = task.payload.get("task", "")
        model = task.payload.get("model", DEFAULT_REASONING_MODEL)

        # ── Load refactor skill prompt ──
        skill_prompt = self.get_skill_prompt("refactor")
        if not skill_prompt:
            skill_prompt = self._skill_prompts.get("refactor", "")
        if not skill_prompt:
            # Fallback: try to load directly from skills/refactor.yaml
            try:
                skills_file = _Path(str(self._project_path)) / "skills" / "refactor.yaml"
                if skills_file.exists():
                    import yaml
                    data = yaml.safe_load(skills_file.read_text())
                    skills_list = data.get("skills", [data]) if isinstance(data, dict) else data
                    for s in skills_list:
                        if s.get("name") == "refactor":
                            skill_prompt = s.get("prompt", "")
                            break
            except Exception:
                pass

        self._log.info(
            "refactor_start",
            target_dirs=target_dirs,
            target_files=target_files,
            refactor_type=refactor_type,
        )

        print(f"\n{'='*60}")
        print(f"  REFACTOR: Code structure improvement")
        print(f"  Targets: {target_dirs or target_files}")
        print(f"  Type: {refactor_type}")
        print(f"{'='*60}\n")

        # ── 1. Baseline: run all existing tests ──
        print("[REFACTOR] Step 1: BASELINE — running full test suite...")
        baseline_pass = False
        baseline_output = ""
        try:
            import subprocess as _sp
            tr = _sp.run(
                ["python3", "-m", "pytest", "tests/", "-q", "--tb=short", "--no-header"],
                capture_output=True, text=True, timeout=300,
                cwd=str(self._project_path),
            )
            baseline_output = (tr.stdout + "\n" + tr.stderr).strip()
            baseline_pass = tr.returncode == 0
            if baseline_pass:
                print("[REFACTOR] Baseline: ALL TESTS GREEN ✓")
            else:
                print("[REFACTOR] Baseline: SOME TESTS FAILING ⚠")
                if len(baseline_output) > 1000:
                    print(f"[REFACTOR] Baseline output:\n{baseline_output[:1000]}")
        except Exception as exc:
            baseline_output = f"Failed to run baseline tests: {exc}"
            print(f"[REFACTOR] Baseline: TEST RUN FAILED — {exc}")

        # ── 2. Build the refactoring task for OpenCode ──
        if task_description:
            refactor_task = task_description
        else:
            refactor_type_descriptions = {
                "duplication": "Find and eliminate code duplication (DRY violations). Extract shared logic into functions/methods.",
                "complexity": "Simplify complex methods: break long methods into smaller ones, replace nested conditionals with guard clauses or polymorphism.",
                "naming": "Improve variable, function, and class names to be more descriptive and follow conventions.",
                "dead_code": "Find and remove dead code: unreachable code, unused imports, unused variables, commented-out blocks.",
                "error_handling": "Add or improve error handling: replace bare excepts, add try/except for I/O operations, add structured logging.",
                "god_class": "Split large classes (>300 lines or >10 methods) by single responsibility.",
                "magic_numbers": "Replace magic numbers and hardcoded strings with named constants or config values.",
                "general": "General code quality improvement: address duplication, complexity, naming, dead code, error handling, type annotations, and import organization.",
            }
            refactor_desc = refactor_type_descriptions.get(
                refactor_type, refactor_type_descriptions["general"]
            )

            target_desc = ""
            if target_files:
                target_desc = f"Target FILES: {', '.join(target_files)}"
            elif target_dirs:
                target_desc = f"Target DIRECTORIES: {', '.join(target_dirs)}"

            refactor_task = (
                f"REFACTORING TASK\n\n"
                f"Type: {refactor_type} — {refactor_desc}\n\n"
                f"{target_desc}\n\n"
                f"CRITICAL: Before starting, run the full test suite:\n"
                f"  python3 -m pytest tests/ -q --tb=short --no-header 2>&1\n\n"
                f"After EVERY change, re-run tests. If ANY previously-green "
                f"test turns red, REVERT immediately.\n\n"
                f"Report: files changed, smells fixed, tests still passing."
            )

        # Prepend skill prompt to the task
        if skill_prompt:
            full_task = f"{skill_prompt}\n\n---\n\n## TASK\n\n{refactor_task}"
        else:
            full_task = refactor_task

        # ── 3. Dispatch to OpenCode ──
        print(f"[REFACTOR] Step 2: Dispatching to OpenCode for refactoring...")
        provider = get_provider("opencode", str(self._project_path))
        provider.shutdown()  # fresh session
        result = await provider.run(
            storage=storage,
            task=full_task[:TASK_MAX_LEN],
            model=model,
        )

        if result.get("status") != "ok":
            err_detail = result.get("errors", "") or result.get("error", "")
            out_detail = result.get("output", "")[-500:]
            self._log.warning("refactor_opencode_failed", error=err_detail[:200])
            print(f"[REFACTOR] OpenCode FAILED: {err_detail[:300]}")
            return TaskResult(
                task_id=task.task_id,
                role=REFACTOR,
                status="failed",
                payload={
                    "error": err_detail[:500],
                    "output_tail": out_detail[:500],
                    "baseline_tests_passed": baseline_pass,
                },
            )

        files_changed = result.get("files_changed", [])
        print(f"[REFACTOR] OpenCode complete. Files changed: {files_changed}")

        # ── 4. Post-refactor: run tests to verify nothing broke ──
        print(f"[REFACTOR] Step 3: VERIFY — running tests after refactoring...")
        tests_pass = False
        test_output = ""
        try:
            import subprocess as _sp
            tr = _sp.run(
                ["python3", "-m", "pytest", "tests/", "-q", "--tb=short", "--no-header"],
                capture_output=True, text=True, timeout=300,
                cwd=str(self._project_path),
            )
            test_output = (tr.stdout + "\n" + tr.stderr).strip()
            tests_pass = tr.returncode == 0
            if tests_pass:
                print("[REFACTOR] Post-refactor: ALL TESTS GREEN ✓")
            else:
                print(f"[REFACTOR] Post-refactor: TESTS FAILED ✗")
                if len(test_output) > 1000:
                    print(f"[REFACTOR] Test output:\n{test_output[:1000]}")
        except Exception as exc:
            test_output = f"Failed to run post-refactor tests: {exc}"
            print(f"[REFACTOR] Test run FAILED: {exc}")

        # ── 5. Run linter ──
        lint_issues = 0
        try:
            import subprocess
            for f in (files_changed or []):
                lint_result = subprocess.run(
                    ["python3", "-m", "flake8", str(_Path(str(self._project_path)) / f)],
                    capture_output=True, text=True, timeout=30,
                    cwd=str(self._project_path),
                )
                if lint_result.returncode != 0:
                    lint_issues += len(lint_result.stdout.strip().split("\n"))
        except Exception:
            pass

        # ── 6. Report ──
        self._log.info(
            "refactor_complete",
            files_changed=len(files_changed or []),
            tests_pass=tests_pass,
            baseline_pass=baseline_pass,
            lint_issues=lint_issues,
        )

        print(f"\n{'='*60}")
        print(f"  REFACTOR COMPLETE")
        print(f"  Files changed: {len(files_changed or [])}")
        print(f"  Tests passing: {tests_pass}")
        print(f"  Lint issues: {lint_issues}")
        print(f"{'='*60}\n")

        return TaskResult(
            task_id=task.task_id,
            role=REFACTOR,
            status="ok" if tests_pass else "warning",
            payload={
                "files_changed": files_changed or [],
                "tests_pass": tests_pass,
                "baseline_tests_passed": baseline_pass,
                "lint_issues": lint_issues,
                "refactor_type": refactor_type,
                "test_output": test_output[:1000] if not tests_pass else "",
            },
        )

    @staticmethod
    def _build_failure_note(attempt: int, stage: str, detail: str) -> str:
        """Build a concise failure note for the task text.

        Appended to the task so the next attempt has context about what
        went wrong in the previous attempt.
        """
        return (
            f"\n\n---\n"
            f"## Previous attempt #{attempt} — FAILED\n"
            f"{detail[:500]}\n"
            f"---"
        )

    @staticmethod
    def _extract_tokens(output: str) -> int:
        """Extract token count from OpenCode output."""
        import json
        try:
            data = json.loads(output) if isinstance(output, str) else output
            return data.get("usage", {}).get("total_tokens", 0)
        except Exception:
            return 0

    @staticmethod
    def _find_test_file(leaf_id: str) -> "Path | None":
        """Find the pytest file for a given leaf element ID. Delegates to TestUtils."""
        from src.agents.test_utils import find_test_file
        return find_test_file(leaf_id)

    # ── Tester ──

    async def _handle_tester(self, task: Task) -> TaskResult:
        from spec_editor_cycle.tester import AcceptanceTester

        from src.storage.filesystem import FilesystemStorage

        storage = FilesystemStorage(self._project_path)
        tester = AcceptanceTester(storage, self._project_path)
        result = await tester.run()
        return TaskResult(
            task_id=task.task_id,
            role=TESTER,
            status=result.get("status", "ok"),
            payload={
                "gaps_found": result.get("gaps_found", 0),
                "leaves_checked": result.get("leaves_checked", 0),
            },
        )

    # ── DevOps ──

    async def _handle_devops(self, task: Task) -> TaskResult:
        """DevOps agent — LLM-driven via skill."""
        from src.storage.filesystem import FilesystemStorage

        storage = FilesystemStorage(self._project_path)

        # Setup LLM with devops skill on first call
        if not self._provider:
            from pathlib import Path as _P

            from src.config.skills import SkillsRegistry

            self._provider = self._make_chat_provider()
            self._max_llm_calls = 8

            # Load devops skill
            skill_paths = []
            skills_dir = _P(str(self._project_path)) / "skills"
            skills_file = _P(str(self._project_path)) / "skills.yaml"
            if skills_dir.is_dir():
                skill_paths.append(skills_dir)
            if skills_file.exists():
                skill_paths.append(skills_file)

            if skill_paths:
                registry = SkillsRegistry(skill_paths)
                devops_skill = registry.get("devops")
                if devops_skill:
                    self._system_prompt = devops_skill.prompt or ""
                    from src.agents.tools import (
                        build_all_handlers,
                        get_tool_definitions,
                    )

                    all_tools = get_tool_definitions(writable=True)
                    self._tools = [
                        t for t in all_tools if t.name in (devops_skill.tools or [])
                    ]
                    self._tool_handlers = build_all_handlers(
                        storage, None, str(self._project_path)
                    )
            if not self._system_prompt:
                self._system_prompt = "You are a DevOps agent. Diagnose build errors."

        # Build task with actual error context
        task_msg = task.payload.get("task", "Build and deploy")
        prompt = f"{task_msg}\n\nPROJECT: {self._project_path}"

        try:
            response = await self.ask(prompt)
            return TaskResult(
                status="ok",
                payload={"output": response[:1000]},
            )
        except Exception as exc:
            self._log.error("devops_llm_failed", error=str(exc))
            return TaskResult(
                status="failed",
                payload={"error": str(exc)},
            )

    # ── Project Manager ──

    async def _handle_project_manager(self, task: Task) -> TaskResult:
        from src.storage.filesystem import FilesystemStorage
        from src.storage.models import Element, ElementStatus

        storage = FilesystemStorage(self._project_path)
        spec_update = task.payload.get("spec_update", "")
        bug_id = task.payload.get("bug_id", "")
        leaf_id = task.payload.get("leaf_id", "")
        issue = task.payload.get("issue", "")
        task_text = task.payload.get("task", "")

        if spec_update:
            import re

            elem_id = task.payload.get("element_id", "")
            if not elem_id:
                m = re.search(r"\b(DEP|INF|MOD|NFR|REQ|SRC)-\d{3,4}\b", spec_update)
                if m:
                    elem_id = m.group(0)
            if elem_id:
                try:
                    existing = storage.read_element(elem_id)
                    existing.content = (
                        (existing.content or "")
                        + "\n\n## Update from PM Agent\n\n"
                        + spec_update
                    )
                    storage.write_element(existing)
                    self._log.info(
                        "pm_updated_element", element_id=elem_id, task_id=task.task_id
                    )
                    return TaskResult(
                        task_id=task.task_id,
                        role="project-manager",
                        status="ok",
                        payload={"element_id": elem_id, "action": "updated"},
                    )
                except Exception:
                    pass
            new_id = elem_id or f"PM-{task.task_id[-8:]}"
            new_elem = Element(
                id=new_id,
                aspect=task.payload.get("aspect", "implementation"),
                element_type=task.payload.get("element_type", "note"),
                title=task.payload.get("title", "PM Agent Update"),
                content=spec_update,
                status=ElementStatus.DRAFT,
                tags=["pm-agent", "auto-generated"],
                derived_from=task.payload.get("derived_from", []),
            )
            try:
                storage.write_element(new_elem)
                self._log.info(
                    "pm_created_element", element_id=new_id, task_id=task.task_id
                )
                return TaskResult(
                    task_id=task.task_id,
                    role="project-manager",
                    status="ok",
                    payload={"element_id": new_id, "action": "created"},
                )
            except Exception as exc:
                self._log.error("pm_write_failed", error=str(exc))
                return TaskResult(
                    task_id=task.task_id,
                    role="project-manager",
                    status="failed",
                    payload={"error": str(exc)},
                )

        # ── Orphaned blocked bugs: no leaf_id, reset to draft for analyst review ──
        if bug_id and not leaf_id:
            action = task.payload.get("action", "")
            if action == "refine_blocked":
                self._log.info("pm_refine_orphaned", bug_id=bug_id)
                try:
                    bug = storage.read_element(bug_id)
                    if bug.status == ElementStatus.BLOCKED:
                        bug.status = ElementStatus.DRAFT
                        bug.tags = [
                            t for t in (bug.tags or [])
                            if t not in ("blocked", "permanent_blocked")
                            and not t.startswith("attempts:")
                        ]
                        bug.tags.append("refined_by_pm")
                        if bug.content:
                            bug.content += (
                                "\n\n## PM Refinement\n\n"
                                "This bug has no derived_from link to a spec element. "
                                "The bug has been reset to draft for analyst review "
                                "to determine which spec element needs refinement."
                            )
                        storage.write_element(bug)
                        self._log.info("pm_orphaned_reset_to_draft", bug_id=bug_id)

                        # Notify analyst-manager about the newly-draft element
                        try:
                            from src.agents.task_queue import AbstractTaskQueue, get_queue_url
                            queue_url = get_queue_url(self._project_path)
                            queue = AbstractTaskQueue.connect(queue_url)
                            await queue.connect()
                            await queue.push(
                                "analyst-manager",
                                {
                                    "action": "spec:refine",
                                    "bug_id": bug_id,
                                    "reason": f"Orphaned blocked bug {bug_id} reset to draft — needs analyst review to link to spec element.",
                                },
                            )
                            await queue.close()
                        except Exception as exc:
                            self._log.error("pm_orphaned_notify_failed", error=str(exc))

                        return TaskResult(
                            task_id=task.task_id,
                            role="project-manager",
                            status="ok",
                            payload={"bug_id": bug_id, "action": "orphaned_reset_to_draft"},
                        )
                except Exception as exc:
                    self._log.error("pm_orphaned_failed", error=str(exc))
                    return TaskResult(
                        task_id=task.task_id,
                        role="project-manager",
                        status="failed",
                        payload={"error": str(exc)},
                    )

        if bug_id and leaf_id:
            action = task.payload.get("action", "")
            if action == "refine_blocked":
                bug_title = task.payload.get("bug_title", "")
                bug_content = task.payload.get("bug_content", "")
                self._log.info("pm_refine_blocked", bug_id=bug_id, leaf_id=leaf_id)

                # ── Check if permanently blocked — refuse reactivation ──
                try:
                    bug = storage.read_element(bug_id)
                    if "permanent_blocked" in bug.tags:
                        self._log.info("pm_permanent_blocked_skip", bug_id=bug_id)
                        return TaskResult(
                            task_id=task.task_id,
                            role="project-manager",
                            status="ok",
                            payload={
                                "bug_id": bug_id,
                                "action": "permanently_blocked",
                                "message": (
                                    f"Bug {bug_id} has been permanently blocked "
                                    "after 2+ BLOCKED→refine cycles. "
                                    "Human review required."
                                ),
                            },
                        )
                except Exception:
                    pass

                # ── Add PM refinement note (no duplicates) ──
                pm_marker = f"## PM Refinement: blocked bug {bug_id}"
                try:
                    el = storage.read_element(leaf_id)
                    if pm_marker not in (el.content or ""):
                        el.content = (el.content or "") + (
                            f"\n\n{pm_marker}\n\n"
                            f"**Bug**: {bug_title}\n\n"
                            f"**Issue**: {bug_content}\n\n"
                            f"**Action**: The coding agent failed after 3 attempts. "
                            f"The requirement likely needs breaking into smaller "
                            f"sub-requirements, concrete acceptance criteria, "
                            f"or clarified integration points.\n\n"
                            f"The bug has been reactivated for re-attempt."
                        )
                        storage.write_element(el)
                        self._log.info(
                            "pm_refinement_note_added",
                            bug_id=bug_id,
                            leaf_id=leaf_id,
                        )
                    else:
                        self._log.info(
                            "pm_refinement_note_skipped_duplicate",
                            bug_id=bug_id,
                        )
                except Exception as exc:
                    self._log.error("pm_refinement_note_failed", error=str(exc))

                # Recover or reactivate bug
                try:
                    bug = storage.read_element(bug_id)
                    # Check if tests pass before reactivating
                    if leaf_id:
                        import subprocess as _sp
                        from pathlib import Path as _Path

                        prefix = leaf_id.lower().replace("-", "")
                        test_dir = _Path(str(self._project_path)) / "tests"
                        for tf in sorted(test_dir.glob(f"test_{prefix}_*.py")):
                            try:
                                tr = _sp.run(
                                    [
                                        "python",
                                        "-m",
                                        "pytest",
                                        str(tf),
                                        "-q",
                                        "--tb=line",
                                        "--no-header",
                                    ],
                                    capture_output=True,
                                    text=True,
                                    timeout=60,
                                    cwd=str(self._project_path),
                                )
                                if tr.returncode == 0:
                                    bug.status = ElementStatus.CONFIRMED
                                    bug.tags = [
                                        t
                                        for t in bug.tags
                                        if t not in ("blocked", "permanent_blocked")
                                        and not t.startswith("attempts:")
                                        and not t.startswith("blocked_cycles:")
                                    ]
                                    bug.tags.append("recovered_by_pm")
                                    storage.write_element(bug)
                                    self._log.info("pm_bug_recovered", bug_id=bug_id)
                                    return TaskResult(
                                        task_id=task.task_id,
                                        role="project-manager",
                                        status="ok",
                                        payload={
                                            "bug_id": bug_id,
                                            "action": "recovered",
                                        },
                                    )
                            except Exception:
                                pass
                    # Tests still fail: reactivate
                    bug.status = ElementStatus.DRAFT
                    bug.tags = [
                        t
                        for t in bug.tags
                        if t not in ("blocked", "permanent_blocked", "seen_by_am")
                        and not t.startswith("attempts:")
                    ]
                    refined_tag = [
                        t for t in bug.tags if t.startswith("refined_count:")
                    ]
                    refined_count = (
                        int(refined_tag[0].split(":")[1]) if refined_tag else 0
                    )
                    refined_count += 1
                    bug.tags = [
                        t for t in bug.tags if not t.startswith("refined_count:")
                    ]
                    bug.tags.append(f"refined_count:{refined_count}")
                    bug.tags.append("refined_by_pm")
                    # ── Add refinement note to the bug itself ──
                    pm_note = (
                        f"\n\n## PM Refinement (attempt {refined_count})\n\n"
                        f"**Bug**: {bug_title}\n\n"
                        f"**Leaf**: {leaf_id}\n\n"
                        f"**Action**: The bug has been reactivated (status → draft) "
                        f"after PM refinement of the linked spec element. "
                        f"The coding agent will re-attempt on the next cycle."
                    )
                    bug.content = (bug.content or "") + pm_note
                    storage.write_element(bug)
                    self._log.info("pm_bug_reactivated", bug_id=bug_id)
                except Exception as exc:
                    self._log.error("pm_bug_reactivate_failed", error=str(exc))
                return TaskResult(
                    task_id=task.task_id,
                    role="project-manager",
                    status="ok",
                    payload={"bug_id": bug_id, "action": "refined_and_reactivated"},
                )

            if action == "escalate_to_analyst":
                # ── Bridge to analyst-manager via EventBus ──
                reason = task.payload.get(
                    "reason", f"Bug {bug_id} needs analyst refinement"
                )
                self._log.info("pm_escalate_to_analyst", bug_id=bug_id, leaf_id=leaf_id)

                # Publish spec:refine event for analyst-manager
                try:
                    from src.agents.events import get_event_bus

                    bus = get_event_bus(str(self._project_path))
                    bus.publish(
                        "spec:refine",
                        {
                            "bug_id": bug_id,
                            "leaf_id": leaf_id,
                            "reason": reason,
                            "source": "project-manager",
                        },
                    )
                    bus.close()
                    self._log.info("pm_published_spec_refine", bug_id=bug_id)
                except Exception as exc:
                    self._log.error("pm_eventbus_failed", error=str(exc))

                # Add escalation note to leaf element
                try:
                    el = storage.read_element(leaf_id)
                    note = f"\n\n## PM Escalation to Analysts: {bug_id}\n\n**Reason**: {reason[:500]}\n\nThe bug has been escalated to analyst-manager for specification refinement."
                    if note not in (el.content or ""):
                        el.content = (el.content or "") + note
                        storage.write_element(el)
                except Exception as exc:
                    self._log.error("pm_note_failed", error=str(exc))

                # Tag the bug as escalated
                try:
                    bug = storage.read_element(bug_id)
                    bug.tags = [t for t in bug.tags if t != "escalated_to_spec_updater"]
                    bug.tags.append("escalated_to_analysts")
                    storage.write_element(bug)
                except Exception:
                    pass

                return TaskResult(
                    task_id=task.task_id,
                    role="project-manager",
                    status="ok",
                    payload={
                        "bug_id": bug_id,
                        "action": "escalated_to_analyst",
                        "event": "spec:refine",
                    },
                )

            try:
                el = storage.read_element(leaf_id)
                el.content = (
                    (el.content or "")
                    + f"\n\n## PM Escalation: {bug_id}\n\nBug requires developer attention.\n\n{issue or 'No additional details.'}"
                )
                storage.write_element(el)
                self._log.info("pm_escalated", bug_id=bug_id, leaf_id=leaf_id)
                return TaskResult(
                    task_id=task.task_id,
                    role="project-manager",
                    status="ok",
                    payload={"bug_id": bug_id, "leaf_id": leaf_id},
                )
            except Exception as exc:
                return TaskResult(
                    task_id=task.task_id,
                    role="project-manager",
                    status="failed",
                    payload={"error": str(exc)},
                )

        # ── Scan for reviewed elements and dispatch to coding queue ──
        action = task.payload.get("action", "")
        if action == "scan_and_dispatch":
            try:
                from spec_editor_cycle.engine import WorkflowEngine
                engine = WorkflowEngine(
                    storage=storage, project_path=str(self._project_path)
                )
                result = await engine._dispatch_to_redis(filter_all_reviewed=True)
                dispatched = result.get("dispatched", 0)
                self._log.info(
                    "pm_dispatched",
                    dispatched=dispatched,
                    skipped_busy=result.get("skipped_busy", 0),
                )
                return TaskResult(
                    task_id=task.task_id,
                    role="project-manager",
                    status="ok",
                    payload={"dispatched": dispatched, **result},
                )
            except Exception as exc:
                self._log.error("pm_dispatch_failed", error=str(exc))
                return TaskResult(
                    task_id=task.task_id,
                    role="project-manager",
                    status="failed",
                    payload={"error": str(exc)},
                )

        self._log.info(
            "pm_task_received",
            task_id=task.task_id,
            task=task_text,
            payload_keys=list(task.payload.keys()),
        )
        return TaskResult(
            task_id=task.task_id,
            role="project-manager",
            status="ok",
            payload={"message": "Task logged for review", "task": task_text},
        )

    # ── Reengineer ──

    async def _handle_reengineer(self, task: Task) -> TaskResult:
        """Reengineer agent — reverse-engineers existing codebase into spec.

        Uses LLM with code-analysis tools to scan the codebase and create
        specification elements documenting existing implementation.
        """
        code_dir = task.payload.get("code_dir", str(self._project_path))
        deep = task.payload.get("deep", False)
        phases = task.payload.get("phases", ["structure", "devops", "api", "ui"])

        self._log.info(
            "reengineer_start",
            code_dir=code_dir,
            deep=deep,
            phases=phases,
        )

        # Terminal-visible progress
        print(f"\n{'='*60}")
        print(f"  REENGINEER: Reverse-engineering codebase into specification")
        print(f"  Code directory: {code_dir}")
        print(f"  Phases: {', '.join(phases)}")
        if deep:
            print(f"  Mode: DEEP (behaviour tracing enabled)")
        print(f"{'='*60}\n")

        skill_prompt = self.get_skill_prompt("reengineer")
        if not skill_prompt:
            skill_prompt = self._skill_prompts.get("reengineer", "")

        if not self._provider:
            print("[ERROR] No LLM provider configured for reengineer.")
            print("  Set SPEC_EDITOR__ANALYST_MODEL env var or configure agents.yaml")
            return TaskResult(
                task_id=task.task_id,
                role="reengineer",
                status="failed",
                payload={"error": "No LLM provider configured"},
            )

        print(f"[INFO] Using model: {getattr(self._provider, '_model', 'unknown')}")
        print(f"[INFO] Tools available: {[t.name for t in self._tools]}")
        print(f"[INFO] Starting analysis... (this may take several minutes)\n")

        # Build task context for the LLM
        # Detect if this is a re-run of the SAME codebase
        # Only skip phases if we already analyzed THIS code_dir before
        is_rerun = False
        if deep:
            try:
                from src.storage.filesystem import FilesystemStorage
                storage = FilesystemStorage(self._project_path)
                imp_count = 0
                for s in storage.list_all():
                    if s.aspect == "implementation":
                        imp_count += 1
                # Only treat as rerun if we already have elements AND
                # the code_dir matches the project root (same codebase)
                is_rerun = imp_count > 5 and (
                    code_dir == str(self._project_path)
                    or code_dir.startswith(str(self._project_path) + "/src")
                )
            except Exception:
                pass

        if is_rerun and deep:
            # Focus ONLY on behaviour — skip already-done phases
            task_msg = (
                f"INCREMENTAL REVERSE ENGINEERING — focus on BEHAVIOUR only.\n\n"
                f"The codebase at **{code_dir}** already has {imp_count} implementation "
                f"elements from previous runs. Structure, APIs, DevOps, and UI are "
                f"already documented.\n\n"
                f"YOUR ONLY TASK: Run Phase 5 (Behaviour Tracing).\n"
                f"- Use search_code to find event handlers (adapt patterns to LANGUAGE).\n"
                f"- For each handler, create a step element (aspect=user_scenarios).\n"
                f"- Group related steps into detailed_scenario flows.\n"
                f"- Link steps to existing UI/module elements via interacts_with/implements.\n"
                f"- Do NOT re-do Phases 1-4 — those elements already exist.\n"
                f"- ALL new elements get status=confirmed.\n"
                f"- Follow deduplication rules: search before create, update over create.\n"
                f"- Report a summary of created behaviour elements."
            )
            phases_focus = ["behaviour"]
        else:
            deep_note = " --deep flag IS SET: run Phase 5 (behaviour tracing) too." if deep else ""
            task_msg = (
                f"Reverse-engineer the codebase at **{code_dir}** into the specification.\n"
                f"Phases to run: {', '.join(phases)}.{deep_note}\n\n"
                f"CRITICAL RULES:\n"
                f"- ALL elements must have status=confirmed.\n"
                f"- Add provenance.source for every element (file:line).\n"
                f"- Use get_file_tree to survey the project first.\n"
                f"- Call search_elements BEFORE creating any element to avoid duplicates.\n"
                f"- UPDATE existing elements instead of creating duplicates.\n"
                f"- After all phases complete, report a summary of created elements."
            )
            phases_focus = phases

        try:
            print("[LLM] Sending task to LLM...")
            response = await self.ask(task_msg)
            self._log.info("reengineer_llm_response", len=len(response))
            print(f"\n[LLM] Response received ({len(response)} chars)")
            print(f"[LLM] First 500 chars: {response[:500]}")
            print(f"\n{'='*60}")
            print(f"  REENGINEER COMPLETE")
            print(f"{'='*60}\n")
            return TaskResult(
                task_id=task.task_id,
                role="reengineer",
                status="ok",
                payload={
                    "summary": response[:2000],
                    "code_dir": code_dir,
                    "phases_completed": phases_focus,
                    "deep": deep,
                    "is_rerun": is_rerun,
                },
            )
        except Exception as exc:
            self._log.error("reengineer_failed", error=str(exc))
            print(f"\n[ERROR] Reengineer failed: {exc}")
            import traceback
            traceback.print_exc()
            return TaskResult(
                task_id=task.task_id,
                role="reengineer",
                status="failed",
                payload={"error": str(exc)},
            )

    # ── Analyst Manager (AM) ──

    async def _handle_analyst_manager(self, task: Task) -> TaskResult:
        """Analyst-manager agent — listens for spec:refine events.

        When a bug is escalated to analysts, analyst-manager:
        1. Reads the bug and affected leaf requirement
        2. Publishes a spec:refine event with full context
        3. Marks the bug for analyst attention

        Full analyst refinement (spawning spec agents) will be added
        when analyst-manager gets LLM access.
        """
        from src.storage.filesystem import FilesystemStorage
        from src.storage.models import ElementStatus

        storage = FilesystemStorage(self._project_path)
        bug_id = task.payload.get("bug_id", "")
        leaf_id = task.payload.get("leaf_id", "")
        action = task.payload.get("action", "")
        reason = task.payload.get("reason", "")

        self._log.info(
            "analyst_manager_task",
            bug_id=bug_id,
            leaf_id=leaf_id,
            action=action,
        )

        # ── refine_blocked: reset blocked → draft for standard analyst pipeline ──
        if action == "refine_blocked" and bug_id:
            try:
                bug = storage.read_element(bug_id)
            except (KeyError, FileNotFoundError):
                # Bug was already deleted (e.g., previous cleanup pass).
                # Return ok so the queue doesn't retry this stale task forever.
                self._log.info("am_refine_skipped_deleted", bug_id=bug_id)
                return TaskResult(
                    task_id=task.task_id,
                    role="analyst-manager",
                    status="ok",
                    payload={"bug_id": bug_id, "action": "skipped_deleted"},
                )

            try:
                if bug.status == ElementStatus.BLOCKED:
                    bug.status = ElementStatus.DRAFT
                    bug.tags = [
                        t for t in (bug.tags or [])
                        if t not in ("blocked", "permanent_blocked")
                        and not t.startswith("attempts:")
                    ]
                    bug.tags.append("refined_by_am")
                    note = (
                        f"\n\n## Analyst-Manager Refinement\n\n"
                        f"**Reason**: {reason or 'Bug blocked after 3 failed fix attempts'}\n\n"
                        f"**Action**: Reset to draft. The standard analyst pipeline "
                        f"(analyst-manager → reviewed → coding agent) will process this element."
                    )
                    bug.content = (bug.content or "") + note
                    storage.write_element(bug)
                    self._log.info("am_refined_blocked", bug_id=bug_id)
                    return TaskResult(
                        task_id=task.task_id,
                        role="analyst-manager",
                        status="ok",
                        payload={"bug_id": bug_id, "action": "blocked_reset_to_draft"},
                    )
                else:
                    # Not blocked — already handled, skip
                    self._log.info("am_refine_skip_not_blocked", bug_id=bug_id, status=bug.status.value)
                    return TaskResult(
                        task_id=task.task_id,
                        role="analyst-manager",
                        status="ok",
                        payload={"bug_id": bug_id, "action": "skipped_not_blocked"},
                    )
            except Exception as exc:
                # Return ok even on unexpected errors — don't clog the queue
                self._log.error("am_refine_blocked_failed", bug_id=bug_id, error=str(exc))
                return TaskResult(
                    task_id=task.task_id,
                    role="analyst-manager",
                    status="ok",
                    payload={"bug_id": bug_id, "error": str(exc)[:200]},
                )

        if bug_id and leaf_id:
            # Read bug and leaf for context
            try:
                bug = storage.read_element(bug_id)
                leaf = storage.read_element(leaf_id)

                # Build context for analysts
                context_lines = [
                    f"## Analyst-Manager: Bug {bug_id} escalated for analyst refinement\n",
                    f"**Bug**: {bug.title}\n",
                    f"**Leaf requirement**: {leaf_id} — {leaf.title}\n",
                    f"**Reason**: {reason or 'Bug blocked after failed fix attempts'}\n",
                ]
                if bug.content:
                    context_lines.append(f"\n### Bug Details\n{bug.content[:1500]}\n")
                if leaf.content:
                    context_lines.append(
                        f"\n### Current Requirement\n{leaf.content[:1500]}\n"
                    )

                context = "\n".join(context_lines)

                # Publish spec:refine event for analyst agents
                try:
                    from src.agents.events import get_event_bus

                    bus = get_event_bus(str(self._project_path))
                    bus.publish(
                        "spec:refine",
                        {
                            "bug_id": bug_id,
                            "leaf_id": leaf_id,
                            "reason": reason,
                            "context": context[:2000],
                            "source": "analyst-manager",
                        },
                    )
                    bus.close()
                    self._log.info("am_published_spec_refine", bug_id=bug_id)
                except Exception as exc:
                    self._log.error("am_eventbus_failed", error=str(exc))

                # Add analyst-manager note to leaf
                am_marker = f"## Analyst-Manager Refinement: {bug_id}"
                if am_marker not in (leaf.content or ""):
                    leaf.content = (
                        leaf.content or ""
                    ) + f"\n\n{am_marker}\n\n{context[:2000]}"
                    storage.write_element(leaf)

                # Tag bug as seen by analyst-manager
                if "seen_by_am" not in (bug.tags or []):
                    bug.tags = list(bug.tags or []) + ["seen_by_am"]
                    storage.write_element(bug)

                return TaskResult(
                    task_id=task.task_id,
                    role="analyst-manager",
                    status="ok",
                    payload={"bug_id": bug_id, "action": "context_built_for_analysts"},
                )
            except Exception as exc:
                self._log.error("am_failed", error=str(exc))
                return TaskResult(
                    task_id=task.task_id,
                    role="analyst-manager",
                    status="failed",
                    payload={"error": str(exc)},
                )

        # ── No specific bug — scan for draft elements via LLM ──
        if action == "scan_drafts" or (not bug_id and not leaf_id):
            if self._provider:
                return await self._scan_drafts_via_llm(storage)
            # Fallback: basic mechanical scan (no LLM)
            draft_count = 0
            for summary in storage.list_all():
                if summary.status.value != "draft":
                    continue
                try:
                    el = storage.read_element(summary.id)
                    if "seen_by_am" not in (el.tags or []):
                        el.tags = list(el.tags or []) + ["seen_by_am"]
                        el.status = ElementStatus.REVIEWED
                        storage.write_element(el)
                        draft_count += 1
                        self._log.info("am_refined_draft", element_id=el.id)
                except Exception:
                    pass

            if draft_count:
                self._log.info("am_scanned_drafts", count=draft_count)
                return TaskResult(
                    task_id=task.task_id,
                    role="analyst-manager",
                    status="ok",
                    payload={"draft_elements_refined": draft_count},
                )

        # ── review_confirmed_bug: analyst reviews fixed bug → deprecate ──
        if action == "review_confirmed_bug" and bug_id:
            try:
                bug = storage.read_element(bug_id)
                affected_id = task.payload.get("affected_requirement", "")

                # Add analyst review note to the bug
                review_note = (
                    f"\n\n## Analyst Review: Fix verified\n\n"
                    f"Bug **{bug_id}** has been fixed and confirmed by QA. "
                    f"The implementation details should be recorded in the "
                    f"structured requirements."
                )
                if affected_id:
                    review_note += f" Affected requirement: {affected_id}."
                    # Also add note to the affected requirement
                    try:
                        req = storage.read_element(affected_id)
                        if f"Bug {bug_id}" not in (req.content or ""):
                            req.content = (req.content or "") + (
                                f"\n\n## Implementation note: {bug_id}\n\n"
                                f"Bug **{bug_id}** ({bug.title}) was fixed. "
                                f"Implementation details from this fix should be "
                                f"incorporated into this requirement.\n"
                            )
                            storage.write_element(req)
                    except Exception:
                        pass

                bug.content = (bug.content or "") + review_note
                bug.tags = [t for t in (bug.tags or []) if t != "seen_by_am"]
                bug.tags.append("analyst-reviewed")
                bug.status = ElementStatus.DEPRECATED  # safe to delete
                storage.write_element(bug)

                self._log.info(
                    "am_deprecated_bug",
                    bug_id=bug_id,
                    affected_requirement=affected_id,
                )
                return TaskResult(
                    task_id=task.task_id,
                    role="analyst-manager",
                    status="ok",
                    payload={
                        "bug_id": bug_id,
                        "action": "deprecated",
                        "message": (
                            f"Bug {bug_id} reviewed and deprecated. "
                            f"Spec update note added to {affected_id}."
                            if affected_id
                            else f"Bug {bug_id} reviewed and deprecated."
                        ),
                    },
                )
            except Exception as exc:
                self._log.error("am_review_failed", bug_id=bug_id, error=str(exc))
                return TaskResult(
                    task_id=task.task_id,
                    role="analyst-manager",
                    status="failed",
                    payload={"error": str(exc)},
                )

        self._log.info("am_noop", task_id=task.task_id)
        return TaskResult(
            task_id=task.task_id,
            role="analyst-manager",
            status="ok",
            payload={"message": "No bug escalation to process"},
        )

    async def _scan_drafts_via_llm(self, storage: Any) -> TaskResult:
        """Use LLM to review each draft element — clean noise, summarize, mark reviewed."""
        from src.storage.models import ElementStatus

        skill_prompt = self.get_skill_prompt("analyst_manager")
        if not skill_prompt:
            skill_prompt = self._skill_prompts.get("analyst-manager", "")

        draft_count = 0
        for summary in storage.list_all():
            if summary.status.value != "draft":
                continue
            try:
                el = storage.read_element(summary.id)
                if "seen_by_am" in (el.tags or []):
                    continue

                task_msg = (
                    f"Review and clean up draft element **{el.id}** ({el.title}).\n\n"
                    f"Current content (may contain noise to remove):\n```\n{el.content[:3000]}\n```\n\n"
                    f"CRITICAL: preserve the LAST (most recent) Previous attempt block "
                    f"with its full error details. Remove older attempts, summarize them. "
                    f"Send COMPLETE new content via write_element — old content is fully replaced. "
                    f"Set status=reviewed, add tag seen_by_am."
                )
                self._log.info("am_llm_review", element_id=el.id)

                try:
                    response = await self.ask(task_msg)
                    self._log.info("am_llm_done", element_id=el.id,
                                   response_len=len(response))
                except Exception as exc:
                    self._log.error("am_llm_failed", element_id=el.id, error=str(exc))
                    # Fallback: basic review
                    el.tags = list(el.tags or []) + ["seen_by_am"]
                    el.status = ElementStatus.REVIEWED
                    storage.write_element(el)
                    draft_count += 1
                    continue

                # LLM should have used write_element tool — check if status changed
                try:
                    el = storage.read_element(summary.id)
                    if el.status == ElementStatus.REVIEWED and "seen_by_am" in (el.tags or []):
                        draft_count += 1
                    else:
                        # LLM didn't update — do it manually
                        el.tags = list(el.tags or []) + ["seen_by_am"]
                        el.status = ElementStatus.REVIEWED
                        storage.write_element(el)
                        draft_count += 1
                except Exception:
                    pass

            except Exception as exc:
                self._log.error("am_scan_error", element_id=summary.id, error=str(exc))

        self._log.info("am_llm_scanned", count=draft_count)
        return TaskResult(
            task_id="am-llm-scan",
            role="analyst-manager",
            status="ok",
            payload={"draft_elements_refined": draft_count},
        )


def _extract_opencode_tokens(output: str) -> dict:
    if not output:
        return {"input": 0, "output": 0}
    tokens_in = 0
    tokens_out = 0
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        usage = event.get("usage", {})
        tokens_in += usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
        tokens_out += usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)
    return {"input": tokens_in, "output": tokens_out}

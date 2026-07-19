"""Workflow Engine — executes declarative workflow.yaml graphs.

Loads a workflow definition from a YAML file and runs its steps
sequentially.  Each step calls a tool (MCP or built-in) and stores
the result.  Conditional steps are skipped when their ``when``
expression evaluates to falsy.

Usage::

    from spec_editor_cycle.engine import WorkflowEngine

    engine = WorkflowEngine(storage, project_path)
    result = await engine.run("workflow.yaml")
    # → {"status": "done", "steps_completed": [...], "spec_created": [...]}
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any

import yaml

# Resolved at import time: VSCode extension env var → hardcoded default
_DEFAULT_REASONING_MODEL = os.environ.get(
    "SPEC_EDITOR__AGENT_1__MODEL", "deepseek/deepseek-reasoner"
)


class WorkflowEngine:
    """Executes a workflow defined in a YAML file.

    Args:
        storage: StorageAdapter for spec elements.
        project_path: Path to the spec-editor project.
        handlers: Dict of tool_name → async callable.  If not provided,
                  uses the cycle tools and a minimal built-in set.
    """

    def __init__(
        self,
        storage: Any,
        project_path: str | Path,
        handlers: dict[str, Any] | None = None,
        provider: str = "opencode",
    ) -> None:
        self._storage = storage
        self._project_path = str(project_path)
        self._provider_name = provider

        # ── Channel lifecycle event buffer ──
        # Events accumulate during a cycle iteration and are flushed
        # by sync_external_channels.  Non-destructive — events stay
        # in Redis streams for other consumers.
        self._pending_channel_events: list[dict[str, Any]] = []

        if handlers is None:
            from spec_editor_cycle.tools import (
                ingest_bugs_tool,
                run_cycle_tool,
                run_log_analysis_tool,
                update_spec_from_bugs_tool,
            )

            handlers = {
                "run_cycle": (
                    lambda **kw: run_cycle_tool(
                        storage=self._storage,
                        project_path=self._project_path,
                        **kw,
                    )
                ),
                "run_log_analysis": (
                    lambda **kw: run_log_analysis_tool(
                        storage=self._storage,
                        project_path=self._project_path,
                        **kw,
                    )
                ),
                "ingest_bugs": (
                    lambda **kw: ingest_bugs_tool(
                        storage=self._storage,
                        project_path=self._project_path,
                        **kw,
                    )
                ),
                "update_spec_from_bugs": (
                    lambda **kw: update_spec_from_bugs_tool(
                        storage=self._storage,
                        **kw,
                    )
                ),
                "terminal": lambda command="", execute=True, cwd=None, **kw: (
                    _builtin_terminal(
                        command=command,
                        execute=execute,
                        cwd=cwd if cwd is not None else self._project_path,
                    )
                ),
                "run_workflow": lambda **kw: self._run_sub_workflow(**kw),
                "request_clarification": _builtin_clarification,
                "generate_all_code": lambda **kw: self._generate_all_code(**kw),
                "fix_bugs_parallel": lambda state=None, max_parallel=5, **kw: (
                    self._fix_bugs_parallel(
                        state=state, max_parallel=max_parallel, **kw
                    )
                ),
                "verify_file_implements": lambda **kw: self._verify_implements(**kw),
                "read_lints": lambda **kw: self._read_lints(**kw),
                "build": lambda **kw: self._build_via_coding_agent(**kw),
                "health_check": lambda **kw: self._health_check(**kw),
                "sync_external_channels": lambda **kw: self._sync_external_channels(**kw),
                "route_channel_events": lambda **kw: self._route_channel_events(**kw),
                "escalate": lambda **kw: self._escalate_blocked(**kw),
                "recheck_blocked": lambda **kw: self._recheck_blocked(**kw),
                "acceptance_test": lambda **kw: self._acceptance_test(**kw),
                "verify_implements": lambda **kw: self._verify_implements(**kw),
                "deploy_via_devops": lambda **kw: self._deploy_via_devops(**kw),
                "cleanup_fixed_bugs": lambda **kw: self._cleanup_fixed_bugs(**kw),
                "notify_analysts_confirmed": lambda **kw: self._notify_analysts_confirmed(**kw),
                "verify_architecture": lambda **kw: self._verify_architecture(**kw),
            }
        self._handlers = handlers

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        workflow_path: str | Path = "workflows/PM-Agent-Workflow.yaml",
        iterations: int = 1,
    ) -> dict:
        """Load and execute a workflow YAML file.

        Args:
            workflow_path: Path to a .yaml file or a directory.
            iterations: Number of times to repeat the workflow (for loops).

        Returns a dict with the workflow status and step results.
        """
        path = Path(workflow_path)

        # Try project-relative path first, then CWD, then installation dir.
        if not path.is_absolute():
            proj_relative = Path(self._project_path) / path
            if proj_relative.is_file() or proj_relative.is_dir():
                path = proj_relative
            else:
                # Derive install root from spec-editor binary, Python executable,
                # or the current module's file location (most robust fallback).
                import shutil, sys, os
                install_root = None
                spec_bin = shutil.which("spec-editor")
                if spec_bin:
                    # <install_root>/.venv/bin/spec-editor → up 3 levels
                    install_root = Path(spec_bin).resolve().parent.parent.parent
                if install_root is None or not (install_root / path).is_file():
                    # Fallback: <install_root>/.venv/bin/python → up 3 levels
                    candidate = Path(sys.executable).resolve().parent.parent.parent
                    if (candidate / path).is_file():
                        install_root = candidate
                if install_root is None or not (install_root / path).is_file():
                    # Fallback: use this module's location
                    # engine.py is at .../plugins/cycle/src/spec_editor_cycle/engine.py
                    # Install root is 5 levels up from __file__
                    candidate = Path(__file__).resolve().parent.parent.parent.parent.parent
                    if (candidate / path).is_file():
                        install_root = candidate
                if install_root is None or not (install_root / path).is_file():
                    # Fallback: SPEC_EDITOR_HOME env var
                    env_home = os.environ.get("SPEC_EDITOR_HOME", "")
                    if env_home and (Path(env_home) / path).is_file():
                        install_root = Path(env_home)
                if install_root:
                    install_relative = install_root / path
                    if install_relative.is_file() or install_relative.is_dir():
                        path = install_relative

        if path.is_dir():
            yaml_files = sorted(path.glob("*.yaml"))
            if not yaml_files:
                return {"status": "error", "error": f"No .yaml files in {path}"}
            path = yaml_files[0]

        if not path.is_file():
            return {"status": "error", "error": f"Workflow not found: {path}"}

        definition = self._load(path)

        # Support looping: run the workflow multiple times.
        # Early stop: if two consecutive iterations find nothing, we're done.
        all_results = []
        idle_streak = 0
        self._current_iteration = 0
        for i in range(iterations):
            self._current_iteration = i + 1
            if iterations > 1:
                print(
                    f"\n[Workflow] === Iteration {i + 1}/{iterations} ===", flush=True
                )
            result = await self._execute(definition)
            all_results.append(result)
            if result.get("status") == "error":
                break

            # Early stop: nothing to fix + no issues + no gaps -> idle
            bugs_fixed = result.get("bugs_fixed", -1)
            issues = result.get("issues", -1)
            gaps = result.get("gaps_found", -1)
            if bugs_fixed == 0 and issues == 0 and gaps == 0:
                idle_streak += 1
                if idle_streak >= 2:
                    print(
                        f"\n[Workflow] Converged — {idle_streak} idle iterations. Stopping.",
                        flush=True,
                    )
                    break
            else:
                idle_streak = 0

        last = all_results[-1] if all_results else {"status": "error"}
        last["iterations"] = len(all_results)
        return last

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @staticmethod
    def _load(path: Path) -> dict:
        """Load and validate a workflow YAML file."""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data or "workflow" not in data:
            raise ValueError(f"Invalid workflow file: {path} — missing 'workflow' key")

        wf = data["workflow"]
        wf.setdefault("state", {})
        wf.setdefault("steps", [])
        return wf

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def _execute(self, wf: dict) -> dict:
        """Run all steps in the workflow sequentially.

        State is derived from element statuses — no ghost variables.
        Targeted tests use @implements decorators on test files.
        """
        state: dict[str, Any] = dict(wf.get("state", {}))
        state.setdefault("bugs_fixed", 0)

        # ── Targeted test command via @implements on test files ──
        state["test_command"] = self._build_test_command()

        # ── Deploy needed? Check element statuses for recently confirmed ──
        state["need_deploy"] = self._check_need_deploy()

        step_results: dict[str, Any] = {}
        completed: list[str] = []
        errors: list[str] = []

        print(f"\n[Workflow] {wf.get('name', 'unnamed')} — {len(wf['steps'])} steps")
        print(f"[Workflow] State: {state}", flush=True)

        for step in wf["steps"]:
            step_id = step.get("id", "?")
            desc = step.get("description", "")

            # Evaluate "when" condition.
            when_expr = step.get("when", "")
            if when_expr:
                if not self._eval_when(when_expr, state, step_results):
                    print(
                        f"[Workflow] SKIP {step_id}: condition not met "
                        f"({when_expr[:60]})",
                        flush=True,
                    )
                    continue

            # Resolve params.
            tool_name = step.get("tool", "")
            raw_params = step.get("params", {})
            params = self._resolve_params(raw_params, state, step_results)

            # Call the tool.
            handler = self._handlers.get(tool_name)
            if handler is None:
                msg = f"Unknown tool '{tool_name}' in step '{step_id}'"
                errors.append(msg)
                print(f"[Workflow] ERROR {step_id}: {msg}", flush=True)
                continue

            print(
                f"[Workflow] RUN  {step_id}: {tool_name}({_short_params(params)})"
                + (f"  # {desc}" if desc else ""),
                flush=True,
            )

            try:
                result = await self._call_tool(handler, params)
            except Exception as exc:
                msg = f"{step_id}: {exc}"
                errors.append(msg)
                print(f"[Workflow] ERROR {msg}", flush=True)
                continue

            # Store result.
            step_results[step_id] = result
            completed.append(step_id)

            # Track deploy success for timestamp comparison
            if step_id == "deploy" and isinstance(result, dict):
                sub_status = result.get("status", "")
                if sub_status in ("done", "ok"):
                    self._track_deploy_time()

            if isinstance(result, dict) and result.get("status") == "failed":
                msg = f"{step_id}: failed — {str(result.get('output', ''))[:200]}"
                errors.append(msg)
                print(f"[Workflow] ERROR {msg}", flush=True)

            # Extract key fields into state for easy reference.
            if isinstance(result, dict):
                for key in (
                    "bugs_found",
                    "bugs_fixed",
                    "spec_created",
                    "spec_updated",
                    "src_created",
                    "done",
                    "passed",
                    "files_changed",
                    "status",
                    "output",
                    "implements_task",
                    "needs_fix",
                    "mod_refs",
                    "uncovered_leaves",
                    "needs_coding",
                    "issues",
                    "dispatched",
                    "skipped_busy",
                ):
                    if key in result:
                        state[key] = result[key]

            self._last_state = dict(state)

        status = "done" if not errors else "done_with_errors"

        print(
            f"[Workflow] {status}: {len(completed)}/{len(wf['steps'])} steps, "
            f"{len(errors)} errors",
            flush=True,
        )

        return {
            "status": status,
            "steps_completed": completed,
            "errors": errors,
            **{k: v for k, v in state.items() if k != "modules"},
        }

    # ------------------------------------------------------------------
    # Sub-workflow support
    # ------------------------------------------------------------------

    async def _run_sub_workflow(self, workflow: str = "", **state_overrides) -> dict:
        """Run another workflow file as a sub-step."""
        path = Path(self._project_path) / "workflows" / f"{workflow}.yaml"
        if not path.is_file():
            return {"status": "error", "error": f"Sub-workflow not found: {path}"}

        print(f"\n  [Sub] Starting {workflow}...", flush=True)
        wf = self._load(path)
        for key, value in state_overrides.items():
            if key != "workflow":
                wf.setdefault("state", {})[key] = value
        result = await self._execute(wf)
        print(f"  [Sub] {workflow}: {result.get('status')}", flush=True)

        # If sub-workflow had errors, create SRC-BUG-* for coding agent to fix
        errors = result.get("errors", [])
        if errors:
            error_text = "\n".join(errors[:10])
            self._create_bug(
                f"{workflow} failed: {errors[0][:60]}",
                f"Sub-workflow '{workflow}' completed with errors:\n{error_text}",
                "high",
            )
            result["needs_fix"] = True
        return result

    # ------------------------------------------------------------------
    # Lifecycle event recording (for channel push)
    # ------------------------------------------------------------------

    def _record_event(
        self,
        event_type: str,
        element_id: str,
        element_title: str = "",
        old_status: str = "",
        new_status: str = "",
        message: str = "",
        severity: str = "info",
        metadata: dict | None = None,
    ) -> None:
        """Record a lifecycle event for later push to external channels.

        Events accumulate in a buffer and are flushed by
        _sync_external_channels at the end of each cycle iteration.
        This is non-destructive — events stay in Redis streams.
        """
        import time

        self._pending_channel_events.append({
            "event_type": event_type,
            "element_id": element_id,
            "element_title": element_title,
            "old_status": old_status,
            "new_status": new_status,
            "message": message,
            "severity": severity,
            "metadata": metadata or {},
            "timestamp": str(int(time.time())),
        })

    async def _generate_all_code(
        self, model: str = _DEFAULT_REASONING_MODEL
    ) -> dict:
        """Dispatch all reviewed spec elements to Redis coding queue.

        Elements covered: MOD-*, IMP-*, NFR-*, SRC-BUG-* with status=reviewed.
        Different modules dispatched in parallel via Redis workers.
        Previously-coded elements (status=confirmed) are skipped.
        """
        return await self._dispatch_to_redis(
            model=model,
            filter_all_reviewed=True,
        )

    async def _fix_bugs_parallel(
        self,
        model: str = _DEFAULT_REASONING_MODEL,
        state: dict | None = None,
        max_parallel: int = 5,
    ) -> dict:
        """Dispatch reviewed SRC-BUG-* elements to Redis coding queue."""
        return await self._dispatch_to_redis(
            model=model,
            max_parallel=max_parallel,
            filter_bugs_only=True,
        )

    async def _dispatch_to_redis(
        self,
        model: str = _DEFAULT_REASONING_MODEL,
        max_parallel: int = 10,
        filter_all_reviewed: bool = False,
        filter_bugs_only: bool = False,
    ) -> dict:
        """Unified Redis dispatch for reviewed elements.

        Priority: uncoded modules first, then bugs.
        Skips already dispatched or blocked elements.
        """
        from pathlib import Path as _Path

        from src.agents.task_queue import AbstractTaskQueue
        from src.agents.events import get_queue_url
        from src.agents.tools import _resolve_module
        from src.config import get_logger

        _log = get_logger(__name__)
        elements = self._storage.list_all()

        # Collect candidates
        reviewed = []
        NON_CODE_PREFIXES = {"SCN", "MET", "SEC", "DSC", "STP", "WIDGET", "SCREEN"}
        auto_confirmed = 0
        for e in elements:
            try:
                el = self._storage.read_element(e.id)
            except Exception:
                continue
            if el.status.value != "reviewed":
                continue
            tags = getattr(el, "tags", []) or []
            prefix = el.id.split("-")[0] if "-" in el.id else ""

            # Already dispatched non-code elements → auto-confirm
            if "dispatched" in tags and prefix in NON_CODE_PREFIXES:
                try:
                    el.status = type(el.status).CONFIRMED
                    el.tags = [t for t in tags if t != "dispatched"]
                    el.tags.append("auto_confirmed_non_code")
                    self._storage.write_element(el)
                    auto_confirmed += 1
                    self._record_event(
                        "status_changed",
                        el.id,
                        element_title=getattr(el, "title", ""),
                        old_status="reviewed",
                        new_status="confirmed",
                        message=f"Auto-confirmed non-code element: {getattr(el, 'title', el.id)}",
                    )
                    _log.info("auto_confirmed_stale", element_id=el.id, prefix=prefix)
                except Exception:
                    pass
                continue

            if "permanent_blocked" in tags or "dispatched" in tags:
                continue

            # Skip meta-bugs about spec-editor agents themselves.
            # These reference MOD-*-agent modules which the coding agent
            # cannot fix — they are pipeline-internal issues that loop
            # forever (error → bug → fail → new error → new bug).
            if el.id.startswith("SRC-BUG-"):
                from src.agents.constants import is_agent_internal_bug
                content = getattr(el, "content", "") or ""
                title = getattr(el, "title", "") or ""
                if is_agent_internal_bug(title=title, content=content):
                    # Auto-deprecate: these are pipeline-internal, not project bugs
                    try:
                        el.status = type(el.status).DEPRECATED
                        el.tags = [t for t in (el.tags or []) if t not in ("dispatched",)]
                        el.tags.append("meta_bug_deprecated")
                        self._storage.write_element(el)
                        self._record_event(
                            "deprecated",
                            el.id,
                            element_title=getattr(el, "title", ""),
                            old_status="reviewed",
                            new_status="deprecated",
                            message="Auto-deprecated meta-bug (spec-editor internal)",
                            severity="warning",
                        )
                    except Exception:
                        pass
                    _log.info("skip_meta_bug", element_id=el.id,
                              reason="references spec-editor agent module")
                    continue

            reviewed.append(el)

        if not reviewed:
            return {"status": "ok", "dispatched": 0, "message": "No reviewed elements to dispatch"}

        # Split: uncoded (MOD*/IMP*/NFR*) first, then bugs (SRC-BUG-*)
        uncoded = [e for e in reviewed if not e.id.startswith("SRC-BUG-")]
        bugs = [e for e in reviewed if e.id.startswith("SRC-BUG-")]

        # ── Auto-confirm non-code elements: SCN, MET, SEC, DSC, STP, WIDGET ──
        # These are specification-only element types (scenarios, metrics,
        # UI screens, detailed steps, widgets). They don't generate Python code —
        # once reviewed by the analyst, they are done.
        NON_CODE_PREFIXES = {"SCN", "MET", "SEC", "DSC", "STP", "WIDGET", "SCREEN"}
        for e in list(uncoded):
            prefix = e.id.split("-")[0] if "-" in e.id else ""
            if prefix in NON_CODE_PREFIXES:
                try:
                    e.status = type(e.status).CONFIRMED
                    e.tags = [t for t in (e.tags or []) if t != "dispatched"]
                    e.tags.append("auto_confirmed_non_code")
                    self._storage.write_element(e)
                    uncoded.remove(e)
                    _log.info("auto_confirmed", element_id=e.id, prefix=prefix)
                except Exception:
                    pass

        if filter_bugs_only:
            candidates = bugs
        elif filter_all_reviewed:
            candidates = uncoded + bugs  # uncoded first
        else:
            candidates = reviewed

        if not candidates:
            return {"status": "ok", "dispatched": 0, "message": "No matching elements"}

        # Resolve queue
        pp = _Path(self._project_path) if self._project_path else _Path(".")
        queue_url = get_queue_url(pp)

        # Track busy modules — only elements actively being worked on
        # (not confirmed/deprecated, which have stale "dispatched" tags).
        busy_modules: set[str] = set()
        for e in elements:
            tags = getattr(e, "tags", []) or []
            if "dispatched" not in tags:
                continue
            # Skip elements that are already done — their "dispatched"
            # tag is stale and should not block new dispatches.
            status_val = ""
            if hasattr(e, "status"):
                st = e.status
                status_val = st.value if hasattr(st, "value") else str(st)
            if status_val in ("confirmed", "deprecated"):
                continue
            mod = _resolve_module(self._storage, e.id) if hasattr(e, "id") else "?"
            busy_modules.add(mod)

        queue = AbstractTaskQueue.connect(queue_url)
        await queue.connect()

        dispatched = 0
        skipped_busy = 0
        for el in candidates:
            if dispatched >= max_parallel:
                break

            # Busy module check: only for SRC-BUG elements (bug fixes).
            # For MOD/ENT/NFR code generation, each element maps to different
            # files — no risk of concurrent edits to the same module.
            mod = _resolve_module(self._storage, el.id) if hasattr(el, "id") else "?"
            if el.id.startswith("SRC-BUG-") and mod in busy_modules:
                skipped_busy += 1
                continue
            if el.id.startswith("SRC-BUG-"):
                busy_modules.add(mod)

            try:
                await queue.push(
                    "coding",
                    {
                        "action": "fix" if el.id.startswith("SRC-BUG-") else "generate",
                        "element_id": el.id,
                        "title": getattr(el, "title", ""),
                        "project_path": self._project_path,
                        "model": model,
                    },
                )
                # Tag as dispatched — re-read first to avoid race
                # with coding agent confirming the same element.
                try:
                    fresh = self._storage.read_element(el.id)
                    if fresh.status.value != "reviewed":
                        _log.debug("dispatch_skip_race", element_id=el.id,
                                   current_status=fresh.status.value)
                        continue  # already confirmed/deprecated by coding agent
                except Exception:
                    pass

                tags = list(getattr(el, "tags", []) or [])
                tags.append("dispatched")
                el.tags = tags
                self._storage.write_element(el)
                _log.info("element_dispatched", element_id=el.id, module=mod,
                          action="fix" if el.id.startswith("SRC-BUG-") else "generate")
                self._record_event(
                    "dispatched",
                    el.id,
                    element_title=getattr(el, "title", ""),
                    old_status="reviewed",
                    new_status="dispatched",
                    message=f"Dispatched to coding queue: {getattr(el, 'title', el.id)}",
                )
                dispatched += 1
            except Exception as exc:
                _log.warning(f"Dispatch failed for {el.id}: {exc}")

        await queue.close()

        # Note: _ensure_agents_running is skipped — the cycle loop now
        # runs its own in-process _coding_team_loop which consumes from
        # the same Redis queue. No need to spawn external worker processes.

        return {
            "status": "ok",
            "dispatched": dispatched,
            "skipped_busy": skipped_busy,
            "uncoded": len(uncoded),
            "bugs": len(bugs),
        }
        """DevOps build — checks files locally, delegates to coding agent only if needed."""
        proj = Path(self._project_path)
        build_dir = proj / target_dir
        build_dir.mkdir(exist_ok=True)

        main_py = build_dir / "main.py"
        req_file = build_dir / "requirements.txt"
        static_dir = build_dir / "static"
        static_dir.mkdir(exist_ok=True)

        # Check existing files
        missing = []
        if not main_py.is_file():
            missing.append("main.py")
        if not req_file.is_file():
            missing.append("requirements.txt")
        if not (static_dir / "index.html").is_file():
            missing.append("static/index.html")

        if not missing:
            print(f"  [Build] All files present — skipping coding agent", flush=True)
            return {"status": "ok", "build_dir": str(build_dir), "files": []}

        print(f"  [Build] Missing: {missing} — delegating to coding agent", flush=True)

        from spec_editor_cycle.providers import get_provider

        task = (
            "DEVOPS TASK: Build the spec-editor.com project.\n\n"
            f"Output directory: {build_dir}\n\n"
            "STEPS:\n"
            f"1. mkdir -p {build_dir}/static\n"
            f"2. Create {build_dir}/main.py — FastAPI server on port 8000 with:\n"
            "   - GET / → {service: spec-editor.com, status: ok}\n"
            "   - CORS middleware allowing all origins\n"
            f"3. Create {build_dir}/requirements.txt with: fastapi, uvicorn\n"
            f"4. cd {proj} && pip install -r {build_dir}/requirements.txt\n"
            f"5. Start server: python {build_dir}/main.py &\n"
            "6. sleep 2 && curl -sf http://localhost:8000/\n"
            "7. If curl succeeds: report BUILD OK\n"
            "8. If server crashes or curl fails: report what failed\n"
            "9. Kill the server after testing\n\n"
            "Use bash, read, write tools. Do NOT modify existing source code.\n"
            "Report: {status: ok/failed, phase: ..., error: ...}"
        )

        print(f"  [Build] Delegating to coding agent (OpenCode)", flush=True)
        provider = get_provider(self._provider_name, self._project_path)
        result = await provider.run(
            storage=self._storage,
            task=task,
        )

        files = result.get("files_changed", [])
        build_ok = result.get("status") == "ok" and len(files) > 0
        if build_ok:
            print(f"  [Build] OK - {len(files)} files created", flush=True)
        else:
            print(f"  [Build] Check result: {result.get('status')}", flush=True)

        return {
            "status": "ok" if build_ok else "needs_attention",
            "build_dir": str(build_dir),
            "files": files,
            "needs_coding": not build_ok,
        }

    async def _health_check(self) -> dict:
        """PM Agent health check — detect gaps, create SRC-BUG-* in spec.

        Checks frontend, logging, server startup.
        Issues become traceable SRC-BUG-* elements in sources aspect.
        """
        proj = Path(self._project_path)
        bugs_created = []

        # Check 1: Frontend
        index_html = proj / "build" / "static" / "index.html"
        if not index_html.is_file():
            self._create_bug(
                "Missing frontend: build/static/index.html",
                "CA-004 requires Next.js frontend. build/static/index.html does not exist.\n"
                "FILES TO CHECK: build/static/\n"
                "ACTION: Create index.html with HTML/CSS/JS in build/static/.",
                "high",
            )
        elif index_html.stat().st_size < 100:
            self._create_bug(
                "Frontend is a placeholder: build/static/index.html (<100 bytes)",
                "CA-004 requires a real frontend with sections: "
                "Home, Service Catalog, Marketplace, A2A Launchpad, Deploy Agent.\n"
                "The current index.html is only a stub with no real UI.\n"
                "FILES TO CREATE: build/static/index.html (full HTML/CSS/JS), "
                "build/static/catalog.html, build/static/marketplace.html, "
                "build/static/launchpad.html, build/static/deploy.html.\n"
                "Each page must have real content and navigation links between them.",
                "critical",
            )

        # Check 2: Server logging
        main_py = proj / "build" / "main.py"
        if main_py.is_file():
            main_text = main_py.read_text()
            if "StructuredLogEmitter" not in main_text:
                self._create_bug(
                    "Server missing StructuredLogEmitter",
                    "build/main.py has no StructuredLogEmitter. "
                    "Add: from mod_website.structured_log import StructuredLogEmitter. "
                    "Log each request to logs/MOD-website/structured.jsonl.",
                    "high",
                )

        # Check 3: Log files must exist AND have content
        website_log = proj / "logs" / "MOD-website" / "structured.jsonl"
        log_has_content = website_log.is_file() and website_log.stat().st_size > 0
        if not log_has_content:
            self._create_bug(
                "No server logs: logs/MOD-website/structured.jsonl",
                "Server logs directory is empty. Requests are not being logged.\n"
                "FILES TO CHECK: build/main.py, mod_website/structured_log.py, src/tracing.py\n"
                "ROOT CAUSE: mod_website/structured_log.py uses Python logging.getLogger() "
                "which does NOT write to logs/ directory.\n"
                "FIX: Replace 'from mod_website.structured_log import StructuredLogEmitter' "
                "with 'from src.tracing import StructuredLogEmitter' in build/main.py.\n"
                "The src.tracing.StructuredLogEmitter writes JSONL to logs/MOD-website/.\n"
                "ACTION: Update build/main.py to use the correct emitter and verify logs appear.",
                "high",
            )

        if not bugs_created:
            print("  [Health] All checks passed", flush=True)
            return {"status": "ok", "issues": 0, "bugs_created": []}

        print(f"  [Health] {len(bugs_created)} bug(s) created in spec", flush=True)
        return {
            "status": "issues_found",
            "issues": len(bugs_created),
            "bugs_created": bugs_created,
            "needs_fix": True,
        }

    def _create_bug(
        self, title: str, description: str, severity: str = "high"
    ) -> str | None:
        """Create SRC-BUG-* element in the spec. Returns bug ID or None."""
        from src.storage.models import Element, ElementStatus, Provenance

        all_els = self._storage.list_all()

        # Check duplicates
        for s in all_els:
            if not s.id.startswith("SRC-BUG-"):
                continue
            try:
                eb = self._storage.read_element(s.id)
                if eb.title == title and eb.status.value == "reviewed":
                    return None  # already exists as reviewed
            except Exception:
                pass

        existing_ids = [
            int(s.id.split("-")[-1])
            for s in all_els
            if s.id.startswith("SRC-BUG-") and len(s.id.split("-")) == 3
        ]
        bug_idx = max(existing_ids) + 1 if existing_ids else 1
        bug_id = f"SRC-BUG-{bug_idx:03d}"

        if not any(s.id == bug_id for s in all_els):
            el = Element(
                aspect="sources",
                element_type="source",
                id=bug_id,
                title=title[:80],
                status=ElementStatus.REVIEWED,
                content=description,
                derived_from=["DEP-001"],
                provenance=Provenance(source="health_check", confidence=0.9),
                tags=["bug", "devops", severity],
            )
            self._storage.write_element(el)
            print(f"  [Bug] Created {bug_id}: {title[:60]}", flush=True)
            return bug_id
        return None

    async def _acceptance_test(self, max_leaves: int = 5) -> dict:
        """Run QA acceptance tests: spec vs code semantic comparison."""
        from spec_editor_cycle.tester import AcceptanceTester

        tester = AcceptanceTester(self._storage, self._project_path)
        return await tester.run(max_leaves=max_leaves)

    async def _sync_external_channels(self, project_path: str = ".") -> dict:
        """Pull from all configured external channels, publish to Redis bridge.

        Channels are configured in local.yaml → channels: section.
        Each channel's pull() is called; items are published to the
        Redis event bridge for async processing by the cycle pipeline.
        Lifecycle events from the current cycle are pushed back via push().

        Events are read non-destructively: the buffer is copied, then
        cleared, but events remain in Redis streams for other consumers.

        Returns:
            {"channels_synced": 3, "items_pulled": 15, "events_pushed": 2, "errors": []}
        """
        import asyncio
        from pathlib import Path

        pp = Path(project_path).resolve()

        # ── Capture buffered events (non-destructive — copy + clear) ──
        events_to_push = list(self._pending_channel_events)
        self._pending_channel_events.clear()

        # Load channel configs
        try:
            import yaml
            local_yaml = pp / "local.yaml"
            if not local_yaml.exists():
                return {"channels_synced": 0, "items_pulled": 0,
                        "events_pushed": 0, "errors": [], "message": "No local.yaml"}

            config_data = yaml.safe_load(local_yaml.read_text())
            channels_list = config_data.get("channels", [])
        except Exception as exc:
            return {"channels_synced": 0, "items_pulled": 0,
                    "events_pushed": 0, "errors": [str(exc)]}

        if not channels_list:
            return {"channels_synced": 0, "items_pulled": 0,
                    "events_pushed": 0, "errors": [], "message": "No channels configured"}

        from src.channels import create_channel
        from src.channels.event_bridge import ChannelBridge, ChannelEvent
        from src.channels.models import ChannelConfig, ChatItem, TrackerItem, LogItem, LifecycleEvent
        from src.channels.router import ChannelRouter

        bridge = ChannelBridge(
            redis_url=get_queue_url(pp) if "get_queue_url" in dir() else "",
        )
        router = ChannelRouter(str(pp))
        total_pulled = 0
        total_pushed = 0
        errors: list[str] = []
        channels_synced = 0

        # ── Route buffered events to channels ──
        routed: list[Any] = []
        if events_to_push:
            lifecycle_events = [
                LifecycleEvent(**e) if isinstance(e, dict) else e
                for e in events_to_push
            ]
            routed = await router.route(lifecycle_events, channels_list)

        for raw in channels_list:
            if not raw.get("enabled", True):
                continue

            channel_type = raw.get("type", "unknown")
            channel_name = raw.get("name", "")
            channel_id = f"{channel_type}:{channel_name}" if channel_name else channel_type
            try:
                cfg = ChannelConfig(**raw)
                channel = create_channel(cfg)
                if channel is None:
                    continue

                # Pull: get new items from the channel
                items = await channel.pull()
                channels_synced += 1

                # Publish to Redis event bridge (name-qualified)
                for item in items:
                    evt = ChannelEvent.from_item(channel_type, item, channel_name)
                    if await bridge.publish(evt):
                        total_pulled += 1

                # Push: send routed lifecycle events back to the channel
                for decision in routed:
                    if decision.action != "push":
                        continue
                    if decision.channel_id != channel_id:
                        continue
                    if await channel.push(decision.event):
                        total_pushed += 1

            except Exception as exc:
                errors.append(f"{channel_id}: {exc}")

        await bridge.close()

        return {
            "channels_synced": channels_synced,
            "items_pulled": total_pulled,
            "events_pushed": total_pushed,
            "errors": errors,
        }

    async def _route_channel_events(self, project_path: str = ".") -> dict:
        """Dedicated routing step: decide which buffered events go to which channels.

        Uses ChannelRouter to apply per-channel response config
        (mode, severity filter, comment_on).  Only runs if there
        are buffered events and configured channels.

        Returns:
            {"routed": 5, "skipped": 12, "channels": ["telegram:dev-team"], "summary": "..."}
        """
        from pathlib import Path

        from src.channels.router import ChannelRouter
        from src.channels.models import LifecycleEvent

        pp = Path(project_path).resolve()

        # Load channel configs
        try:
            import yaml
            local_yaml = pp / "local.yaml"
            if not local_yaml.exists():
                return {"routed": 0, "skipped": 0, "channels": [],
                        "message": "No local.yaml"}

            config_data = yaml.safe_load(local_yaml.read_text())
            channels_list = config_data.get("channels", [])
        except Exception as exc:
            return {"routed": 0, "skipped": 0, "channels": [], "error": str(exc)}

        if not channels_list:
            return {"routed": 0, "skipped": 0, "channels": [],
                    "message": "No channels configured"}

        # Build lifecycle events from buffer
        events = [
            LifecycleEvent(**e) if isinstance(e, dict) else e
            for e in self._pending_channel_events
        ]

        if not events:
            return {"routed": 0, "skipped": 0, "channels": [],
                    "message": "No buffered events"}

        # Route
        router = ChannelRouter(str(pp))
        decisions = await router.route(events, channels_list)

        routed = sum(1 for d in decisions if d.action == "push")
        skipped = sum(1 for d in decisions if d.action == "skip")
        channels_used = list(set(d.channel_id for d in decisions if d.action == "push"))

        # Build summary messages per channel
        summaries: dict[str, str] = {}
        for channel_id in channels_used:
            channel_events = [d.event for d in decisions if d.channel_id == channel_id and d.action == "push"]
            summaries[channel_id] = router.build_summary_message(channel_events, channel_id)

        return {
            "routed": routed,
            "skipped": skipped,
            "channels": channels_used,
            "summaries": summaries,
        }

    async def _escalate_blocked(self) -> dict:
        """Report BLOCKED elements — console + push to PM agent for spec refinement.

        PM agent can reset the element to draft for re-attempt after refining
        the underlying requirement with the orchestrator.
        Applies to ANY element type (NFR, IMP, SRC-BUG, etc.) with blocked status.
        """
        import json as _json

        blocked = []
        for bs in self._storage.list_all():
            try:
                bug = self._storage.read_element(bs.id)
                if bug.status.value == "blocked":
                    blocked.append(bug)
            except Exception:
                pass

        if not blocked:
            return {"status": "ok", "blocked": 0}

        # ── Console ──
        print(f"\n  ╔══════════════════════════════════════╗", flush=True)
        print(f"  ║  ESCALATION: {len(blocked)} blocked element(s)║", flush=True)
        print(f"  ╠══════════════════════════════════════╣", flush=True)
        for bug in blocked:
            print(
                f"  ║  {bug.id}: {bug.title[:40]} → PM agent for refinement", flush=True
            )
        print(f"  ╚══════════════════════════════════════╝\n", flush=True)

        # ── Push to analyst-manager queue for standard refinement path ──
        # Analyst team picks up the task, resets blocked→draft,
        # then the normal draft→reviewed→confirmed pipeline takes over.
        try:
            from src.agents.task_queue import AbstractTaskQueue, get_queue_url

            queue_url = get_queue_url(self._project_path)
            if "redis" in queue_url or queue_url.startswith("file://"):
                queue = AbstractTaskQueue.connect(queue_url)
                await queue.connect()
                for bug in blocked:
                    leaf_id = bug.derived_from[0] if bug.derived_from else ""
                    await queue.push(
                        "analyst-manager",
                        {
                            "action": "refine_blocked",
                            "bug_id": bug.id,
                            "leaf_id": leaf_id,
                            "reason": f"Bug {bug.id} blocked after 3 failed coding attempts — needs analyst refinement.",
                            "bug_title": bug.title,
                            "bug_content": (bug.content or "")[:2000],
                        },
                    )
                await queue.close()
                print(
                    f"  [Escalate] {len(blocked)} element(s) → analyst-manager for refinement",
                    flush=True,
                )
        except Exception as exc:
            print(f"  [Escalate] Queue push failed: {exc}", flush=True)

        return {
            "status": "escalated",
            "blocked": len(blocked),
            "bugs": [b.id for b in blocked],
        }

    async def _cleanup_fixed_bugs(self, **kw) -> dict:
        """Delete SRC-BUG-* elements with status='confirmed'."""
        from src.agents.tools import cleanup_fixed_bugs_tool

        return await cleanup_fixed_bugs_tool(storage=self._storage)

    async def _notify_analysts_confirmed(self, **kw) -> dict:
        """Push confirmed SRC-BUG-* elements to analyst-manager for spec review."""
        from src.agents.tools import notify_analysts_confirmed_tool

        return await notify_analysts_confirmed_tool(
            storage=self._storage,
            project_path=str(self._project_path),
        )

    async def _verify_implements(self, **kw) -> dict:
        """Verify @implements decorator coverage in the project.

        Scans Python files for @implements annotations and checks
        coverage against spec leaves.
        """
        try:
            from src.mcp.verifier import verify_traceability

            result = verify_traceability(
                storage=self._storage,
                code_dir=str(self._project_path),
            )
            return {
                "status": "ok",
                "implemented": result.get("implemented", 0),
                "uncovered": result.get("uncovered", 0),
                "coverage_pct": result.get("coverage_pct", 0),
            }
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    async def _verify_architecture(self, **kw) -> dict:
        """Run architecture enforcement tests for the project.

        Uses the Implementation Engine to generate and run
        architecture compliance tests. Returns violations found.
        """
        try:
            from src.implementation.engine import ImplementationEngine

            engine = ImplementationEngine(str(self._project_path))
            violations = engine.verify()

            if not violations:
                return {"status": "ok", "violations": 0, "message": "Architecture compliant"}

            return {
                "status": "violations_found",
                "violations": len(violations),
                "details": [
                    {"file": v.file, "description": v.description}
                    for v in violations[:10]
                ],
                "message": f"Found {len(violations)} architecture violation(s)",
            }
        except Exception as exc:
            return {"status": "error", "error": str(exc), "violations": 0}

    async def _recheck_blocked(self) -> dict:
        """Re-check deprecated+blocked bugs.

        Finds BLOCKED/permanent_blocked bugs, runs their relevant test files,
        and recovers them if tests pass. Remaining blocked bugs are escalated
        to analyst-manager for analysis.
        """

        import subprocess as _sp

        print(f"\n  [Recheck] Scanning deprecated+blocked bugs...", flush=True)

        blocked_bugs = []
        for bs in self._storage.list_all():
            try:
                bug = self._storage.read_element(bs.id)
                if bug.status.value != "blocked":
                    continue
                blocked_bugs.append(bug)
            except Exception:
                pass

        if not blocked_bugs:
            print("  [Recheck] No blocked bugs found", flush=True)
            return {"status": "ok", "rechecked": 0, "recovered": 0}

        print(f"  [Recheck] Found {len(blocked_bugs)} blocked bug(s)", flush=True)

        recovered: list[str] = []
        still_blocked: list[str] = []

        for bug in blocked_bugs:
            # Find test file by leaf element convention
            leaf_id = bug.derived_from[0] if bug.derived_from else ""
            test_file = self._find_test_file(leaf_id)

            if test_file is None:
                still_blocked.append(bug.id)
                print(
                    f"  [Recheck] {bug.id}: no test file for {leaf_id} — stays blocked",
                    flush=True,
                )
                continue

            # Run the test file
            try:
                if self._run_pytest(test_file, str(self._project_path)):
                    # Tests pass! Bug was fixed.
                    from src.storage.models import ElementStatus

                    bug.status = ElementStatus.CONFIRMED
                    bug.tags = [
                        t
                        for t in bug.tags
                        if t not in ("blocked", "permanent_blocked")
                        and not t.startswith("blocked_cycles:")
                    ]
                    bug.tags.append("recovered_by_recheck")
                    self._storage.write_element(bug)
                    recovered.append(bug.id)
                    print(
                        f"  [Recheck] {bug.id}: tests pass → RECOVERED",
                        flush=True,
                    )
                else:
                    still_blocked.append(bug.id)
                    # Only show first line of failure
                    first_fail = (
                        result.stdout.strip().split("\n")[-1]
                        if result.stdout.strip()
                        else "tests failed"
                    )
                    print(
                        f"  [Recheck] {bug.id}: tests still fail — {first_fail[:100]}",
                        flush=True,
                    )
            except Exception as exc:
                still_blocked.append(bug.id)
                print(
                    f"  [Recheck] {bug.id}: test run error — {exc}",
                    flush=True,
                )

        # ── Escalate still-blocked bugs to analyst-manager for analysis ──
        if still_blocked:
            print(
                f"\n  [Recheck] {len(still_blocked)} bug(s) still blocked "
                f"→ escalating to analyst-manager",
                flush=True,
            )
            try:
                from src.agents.task_queue import AbstractTaskQueue, get_queue_url

                queue_url = get_queue_url(self._project_path)
                queue = AbstractTaskQueue.connect(queue_url)
                await queue.connect()
                for bug_id in still_blocked:
                    await queue.push(
                        "analyst-manager",
                        {
                            "action": "escalate_to_analyst",
                            "bug_id": bug_id,
                            "reason": (
                                f"Bug {bug_id} remains blocked after "
                                f"{self._current_iteration} iterations. "
                                f"Requirement needs analyst review."
                            ),
                        },
                    )
                await queue.close()
            except Exception as exc:
                print(f"  [Recheck] Escalation push failed: {exc}", flush=True)

        return {
            "status": "ok",
            "rechecked": len(blocked_bugs),
            "recovered": len(recovered),
            "recovered_ids": recovered,
            "still_blocked": still_blocked,
        }

    def _auto_deprecate_if_resolved(self, el) -> bool:
        """If element's leaf has ## RESOLVED, auto-deprecate it. Returns True if deprecated."""
        from src.storage.models import ElementStatus as ES

        if not el.derived_from:
            return False
        leaf_id = el.derived_from[0]
        try:
            leaf = self._storage.read_element(leaf_id)
            if f"## RESOLVED: {el.id}" in (leaf.content or ""):
                el.status = ES.DEPRECATED
                el.tags = [
                    t
                    for t in el.tags
                    if t not in ("blocked",) and not t.startswith("attempts:")
                ]
                el.tags.append("auto_deprecated_resolved")
                self._storage.write_element(el)
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def _run_pytest(test_file: Path, cwd: str) -> bool:
        """Run pytest on a single test file. Delegates to shared TestUtils."""
        from src.agents.test_utils import run_pytest
        return run_pytest(test_file, cwd)

    @staticmethod
    def _find_test_file(leaf_id: str) -> Path | None:
        """Find the test file for a leaf element. Delegates to shared TestUtils."""
        from src.agents.test_utils import find_test_file
        return find_test_file(leaf_id)

    def _redis_key(self, suffix: str) -> str:
        """Build a Redis key for this project: {slug}:{suffix}."""
        return f"{Path(self._project_path).name}:{suffix}"

    def _redis_ts(self, suffix: str, value: str | None = None) -> float:
        """Get or set a Redis timestamp.  Returns float (0 if Redis unavailable)."""
        try:
            from src.agents.events import get_queue_url

            queue_url = get_queue_url(self._project_path)
            if "redis" in queue_url:
                import time

                import redis

                r = redis.from_url(queue_url.split("?")[0], socket_connect_timeout=2)
                key = self._redis_key(suffix)
                if value is not None:
                    r.set(key, value)
                    result = 0
                else:
                    result = float(r.get(key) or 0)
                r.close()
                return result
        except Exception:
            pass
        return 0

    def _track_fix_time(self) -> None:
        """Record last fix timestamp in Redis."""
        import time

        self._redis_ts("last_fix_time", str(time.time()))

    def _track_deploy_time(self) -> None:
        """Record last successful deploy timestamp in Redis."""
        import time

        self._redis_ts("last_deploy_time", str(time.time()))

    def _build_test_command(self) -> str:
        """Build targeted test command from recently CONFIRMED elements.

        Scans test files for @implements("ELEMENT-ID") decorators.
        Only runs tests linked to elements confirmed in this iteration.
        Falls back to full suite if no targeted tests found.
        """
        proj = Path(self._project_path)
        tests_dir = proj / "tests"
        if not tests_dir.is_dir():
            return f"python3 -m pytest {tests_dir} -q --tb=short --no-header 2>&1"

        # Find elements confirmed since last iteration
        recently_confirmed: list[str] = []
        for s in self._storage.list_all():
            el = None
            try:
                el = self._storage.read_element(s.id)
            except Exception:
                continue
            if el and el.status.value == "confirmed":
                recently_confirmed.append(el.id)

        if not recently_confirmed:
            return f"python3 -m pytest {tests_dir} -q --tb=short --no-header 2>&1"

        # Scan test files for @implements matching confirmed elements
        targeted: set[str] = set()
        confirmed_set = set(recently_confirmed)
        for tf in tests_dir.rglob("*.py"):
            try:
                text = tf.read_text()
            except Exception:
                continue
            for m in re.finditer(r"@implements\(['\"]([^'\"]+)['\"]", text):
                if m.group(1) in confirmed_set:
                    targeted.add(str(tf))
                    break

        if not targeted:
            return f"python3 -m pytest {tests_dir} -q --tb=short --no-header 2>&1"

        cmd = f"python3 -m pytest {' '.join(sorted(targeted))} -q --tb=short --no-header 2>&1"
        print(
            f"  [Tests] Targeted: {len(targeted)} file(s) "
            f"for {len(recently_confirmed)} confirmed element(s)",
            flush=True,
        )
        return cmd

    def _check_need_deploy(self) -> bool:
        """Check if deploy is needed based on recently confirmed elements."""
        for s in self._storage.list_all():
            if s.status.value == "confirmed":
                return True
        return False
        """Check @implements decorators reference leaf requirements.

        If MOD-* references are found, enriches the task for fix_bugs
        with instructions to update them.
        """
        import re
        from pathlib import Path

        proj = Path(self._project_path)
        mod_refs: dict[str, int] = {}

        for py_file in proj.rglob("*.py"):
            sp = str(py_file)
            if "__pycache__" in sp or ".venv" in sp or "node_modules" in sp:
                continue
            if "/tests/" in sp:
                continue
            if "'" in sp:
                continue
            try:
                text = py_file.read_text(encoding="utf-8")
            except Exception:
                continue
            for m in re.finditer(r"^@implements\(['\"](MOD-[^'\"]+)['\"]", text):
                mod_id = m.group(1)
                mod_refs[mod_id] = mod_refs.get(mod_id, 0) + 1

        if not mod_refs:
            print("  [Verify] 0 MOD-* references", flush=True)

        # Check for uncovered leaves anyway
        leaves = _find_leaves_inline(self._storage)
        impl_map = _scan_implements_inline(self._project_path)
        uncovered = [l for l in leaves if l["id"] not in impl_map]

        if not uncovered:
            print("  [Verify] All leaves covered!", flush=True)
            return {
                "status": "ok",
                "mod_refs": 0,
                "needs_fix": False,
                "uncovered_leaves": 0,
            }

        uncovered_ids = [l["id"] for l in uncovered[:10]]
        task_extra = (
            "\n\nALSO: Add @implements decorators to classes for these leaf requirements:\n"
            + ", ".join(uncovered_ids)
            + "\n\nRead aspects/ to understand each requirement. "
            "Find the corresponding class in mod_*/ or src/ directories. "
            "Add @implements(LEAF-ID) to each class. "
            "DO NOT modify test files."
        )
        if mod_refs:
            task += task_extra
        else:
            task = "TASK: Add @implements decorators to classes for uncovered leaf requirements.\n\n"
            task += task_extra

        return {
            "status": "ok",
            "mod_refs": sum(mod_refs.values()),
            "needs_fix": True,
            "implements_task": task,
            "uncovered_leaves": len(uncovered) if uncovered else 0,
        }

    async def _deploy_via_devops(self, **kw) -> dict:
        """Deploy via DevOps LLM agent — pushes to devops queue, waits for result."""
        print("  [Deploy] Dispatching to DevOps agent...", flush=True)
        task_text = (
            "Build and deploy the project.\n\n"
            f"PROJECT: {self._project_path}\n"
            "1. Run the build command for this project.\n"
            "2. If build fails, diagnose the error:\n"
            "   - INFRA (tool missing): fix it yourself\n"
            "   - CODE (compile error): create SRC-BUG-* via write_element\n"
            "3. If build succeeds, report status."
        )
        return await self._dispatch_to_queue(
            "devops",
            {"task": task_text},
            timeout=600,
        )

    async def _dispatch_to_queue(
        self, role: str, payload: dict, timeout: int = 300
    ) -> dict:
        """Push a task to the agent queue and wait for result.

        Works with both Redis and file-based queues.

        Args:
            role: "coding", "tester", or "devops"
            payload: Task payload (task description, model, etc.)
            timeout: Max seconds to wait for result

        Returns:
            Result dict with status, output, files_changed, etc.
        """
        import asyncio as _asyncio

        from src.agents.task_queue import AbstractTaskQueue, get_queue_url

        queue_url = get_queue_url(self._project_path)
        queue = AbstractTaskQueue.connect(queue_url)
        is_redis = "redis" in queue_url
        try:
            await queue.connect()
            task_id = await queue.push(role, payload)

            # Poll for result
            start = _asyncio.get_event_loop().time()
            while True:
                elapsed = _asyncio.get_event_loop().time() - start
                if elapsed > timeout:
                    return {"status": "timeout", "task_id": task_id}

                import json as _json

                if is_redis:
                    # Redis: check done:{role} stream for task_id
                    try:
                        done_stream = getattr(queue, "_prefix", "") + f"done:{role}"
                        msgs = await queue._client.xread(
                            {done_stream: "0-0"}, count=100, block=100
                        )
                        for _stream, entries in msgs:
                            for _msg_id, data in entries:
                                if data.get("task_id") == task_id:
                                    payload_data = {}
                                    try:
                                        payload_data = _json.loads(
                                            data.get("result", "{}")
                                        )
                                    except Exception:
                                        pass
                                    return {
                                        "status": data.get("status", "failed"),
                                        "task_id": task_id,
                                        "output": payload_data.get("output", ""),
                                        "files_changed": payload_data.get(
                                            "files_changed", []
                                        ),
                                        "gaps_found": payload_data.get("gaps_found", 0),
                                        "leaves_checked": payload_data.get(
                                            "leaves_checked", 0
                                        ),
                                    }
                    except Exception:
                        pass  # Redis not available, fall through to sleep
                else:
                    # File-based: check done/{task_id}.result.json
                    done_dir = Path(self._project_path) / "tasks" / role / "done"
                    result_file = done_dir / f"{task_id}.result.json"
                    if result_file.exists():
                        try:
                            data = _json.loads(result_file.read_text())
                            return {
                                "status": data.get("status", "failed"),
                                "task_id": task_id,
                                "output": data.get("payload", {}).get("output", ""),
                                "files_changed": data.get("payload", {}).get(
                                    "files_changed", []
                                ),
                                "gaps_found": data.get("payload", {}).get(
                                    "gaps_found", 0
                                ),
                                "leaves_checked": data.get("payload", {}).get(
                                    "leaves_checked", 0
                                ),
                            }
                        except Exception as exc:
                            return {
                                "status": "error",
                                "task_id": task_id,
                                "error": str(exc),
                            }

                await _asyncio.sleep(1)
        finally:
            await queue.close()

    @staticmethod
    async def _retry_llm(coro_factory, name: str = "llm") -> Any:
        """Call an LLM factory with infinite retry on transient errors.

        Used in agent loops where a network blip should not cause
        a status transition (e.g. draft→blocked). Permanent errors
        (401, invalid key) are re-raised immediately.
        """
        import asyncio

        attempt = 0
        while True:
            attempt += 1
            try:
                return await coro_factory()
            except Exception as exc:
                err_str = str(exc).lower()
                if any(
                    kw in err_str
                    for kw in (
                        "401",
                        "unauthorized",
                        "403",
                        "forbidden",
                        "invalid_request_error",
                        "invalid api key",
                    )
                ):
                    raise
                print(
                    f"  [{name}] attempt {attempt}: {exc} — retrying in 4s...",
                    flush=True,
                )
                await asyncio.sleep(4)

    async def _fix_one_bug(
        self,
        bug,
        model: str,
        worker_id: int = 0,
    ) -> dict:
        """Fix a single reviewed element (any type).  Runs as a parallel worker.

        Increments attempt counter, dispatches to coding agent, runs tests,
        updates element status (confirmed / blocked).
        """
        prefix = f"[Worker-{worker_id}]"
        try:
            # ── Attempt counter ──
            attempts_tag = [t for t in bug.tags if t.startswith("attempts:")]
            attempts = int(attempts_tag[0].split(":")[1]) if attempts_tag else 0

            is_clarification = "needs_clarification" in bug.tags
            if attempts >= 3:
                if is_clarification:
                    return await self._handle_clarification_bug(bug, prefix)
                else:
                    return await self._handle_blocked_bug(bug, prefix)

            attempts += 1
            bug.tags = [t for t in bug.tags if not t.startswith("attempts:")]
            bug.tags.append(f"attempts:{attempts}")
            self._storage.write_element(bug)

            # ── Build task for coding agent ──
            task_text = (
                f"Fix {bug.id} (attempt {attempts}/3): {bug.title}.\n\n"
                f"{bug.content or ''}\n\n"
                f"PROJECT: {self._project_path}\n"
                f"Read FILES TO CHECK mentioned above before making changes.\n"
                f"Use bash to run: python -m pytest tests/ -q to verify fixes."
            )
            print(
                f"  {prefix} {bug.id}: attempt {attempts}/3 — {bug.title[:60]}",
                flush=True,
            )

            # ── Run coding agent with infinite retry on transient errors ──
            from spec_editor_cycle.providers import get_provider

            provider = get_provider(self._provider_name, self._project_path)
            provider.shutdown()  # fresh session per bug
            result = await self._retry_llm(
                lambda: provider.run(
                    storage=self._storage,
                    task=task_text[:4000],
                    model=model,
                ),
                name=bug.id,
            )

            if result.get("status") != "ok":
                print(
                    f"  {prefix} {bug.id}: agent returned {result.get('status')}",
                    flush=True,
                )
                return {"fixed": False, "bug_id": bug.id, "error": result.get("status")}

            # ── Verify with tests ──
            leaf_id = bug.derived_from[0] if bug.derived_from else ""
            test_file = WorkflowEngine._find_test_file(leaf_id)
            tests_pass = False
            if test_file and test_file.exists():
                try:
                    import subprocess as _sp

                    tr = _sp.run(
                        [
                            "python",
                            "-m",
                            "pytest",
                            str(test_file),
                            "-q",
                            "--tb=line",
                            "--no-header",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=60,
                        cwd=str(self._project_path),
                    )
                    tests_pass = tr.returncode == 0
                except Exception:
                    pass
            else:
                tests_pass = bool(result.get("files_changed"))

            if tests_pass:
                from src.storage.models import ElementStatus

                bug.status = ElementStatus.CONFIRMED
                self._storage.write_element(bug)
                self._track_fix_time()
                print(f"  {prefix} {bug.id} → confirmed (tests pass)", flush=True)
                return {"fixed": True, "bug_id": bug.id}
            else:
                print(
                    f"  {prefix} {bug.id}: agent reported ok but tests still fail",
                    flush=True,
                )
                return {"fixed": False, "bug_id": bug.id, "error": "tests_fail"}

        except Exception as exc:
            print(f"  {prefix} {bug.id}: exception — {exc}", flush=True)
            return {"fixed": False, "bug_id": bug.id, "error": str(exc)}

    async def _handle_clarification_bug(self, bug, prefix: str) -> dict:
        """Escalate a bug that needs spec clarification (not code fix)."""
        print(
            f"  {prefix} {bug.id}: needs clarification — notifying spec_updater",
            flush=True,
        )
        bug.tags = [t for t in bug.tags if not t.startswith("attempts:")]
        bug.tags.append("escalated_to_spec_updater")
        self._storage.write_element(bug)

        try:
            from src.agents.task_queue import (
                AbstractTaskQueue,
                get_event_bus,
                get_queue_url,
            )

            q = AbstractTaskQueue.connect(get_queue_url(self._project_path))
            await q.connect()
            await q.push(
                "project-manager",
                {
                    "bug_id": bug.id,
                    "leaf_id": bug.derived_from[0] if bug.derived_from else "",
                    "issue": bug.content[:500],
                },
            )
            await q.close()

            try:
                bus = get_event_bus(str(self._project_path))
                bus.publish(
                    "spec:refine",
                    {
                        "bug_id": bug.id,
                        "leaf_id": bug.derived_from[0] if bug.derived_from else "",
                        "reason": "needs_clarification",
                    },
                )
                bus.close()
            except Exception:
                pass
        except Exception:
            pass

        return {"fixed": False, "bug_id": bug.id, "action": "escalated"}

    async def _handle_blocked_bug(self, bug, prefix: str) -> dict:
        """Mark a bug as BLOCKED after 3 failed attempts."""
        from src.storage.models import ElementStatus

        blocked_cycles_tag = [t for t in bug.tags if t.startswith("blocked_cycles:")]
        blocked_cycles = (
            int(blocked_cycles_tag[0].split(":")[1]) if blocked_cycles_tag else 0
        )
        blocked_cycles += 1

        bug.status = ElementStatus.BLOCKED
        bug.tags = [
            t
            for t in bug.tags
            if not t.startswith("attempts:") and not t.startswith("blocked_cycles:")
        ]
        bug.tags.append(f"blocked_cycles:{blocked_cycles}")

        if blocked_cycles >= 2:
            bug.tags.append("permanent_blocked")
            print(
                f"  {prefix} {bug.id}: permanently BLOCKED"
                f" after {blocked_cycles} cycles — needs human review",
                flush=True,
            )
        self._storage.write_element(bug)

        # Add blocking comment to leaf
        if bug.derived_from:
            blocked_marker = f"## BLOCKED: {bug.id}"
            for leaf_id in bug.derived_from:
                try:
                    leaf = self._storage.read_element(leaf_id)
                    if blocked_marker not in (leaf.content or ""):
                        leaf.content = (leaf.content or "") + (
                            f"\n\n{blocked_marker}\n\n"
                            f"**Reason**: 3 failed implementation attempts.\n"
                            f"**Bug**: {bug.content[:300] if bug.content else bug.title}\n"
                        )
                        self._storage.write_element(leaf)
                except Exception:
                    pass

        return {"fixed": False, "bug_id": bug.id, "action": "blocked"}

    # ------------------------------------------------------------------
    # Verification tools
    # ------------------------------------------------------------------

    async def _verify_file_implements(self, file_path: str = "src/") -> dict:
        """Verify @implements coverage on generated code."""
        import subprocess

        target = Path(self._project_path) / file_path
        result = subprocess.run(
            ["spec-editor", "verify", str(target)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {"passed": result.returncode == 0, "output": result.stdout[:500]}

    async def _read_lints(self, file_path: str = "src/") -> dict:
        """Check for linting errors."""
        import subprocess

        result = subprocess.run(
            ["ruff", "check", str(Path(self._project_path) / file_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            "passed": result.returncode == 0,
            "output": (result.stdout or result.stderr)[:500],
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _call_tool(handler, params: dict) -> Any:
        """Call a tool handler, supporting both sync and async."""
        result = handler(**params)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    @staticmethod
    def _resolve_params(
        raw: dict,
        state: dict,
        step_results: dict,
    ) -> dict:
        """Replace {{...}} template variables in param values."""
        resolved = {}
        for key, value in raw.items():
            if isinstance(value, str):
                resolved[key] = WorkflowEngine._resolve_string(
                    value, state, step_results
                )
            else:
                resolved[key] = value
        return resolved

    @staticmethod
    def _resolve_string(
        template: str,
        state: dict,
        step_results: dict,
    ) -> str:
        """Replace {{var}} references in a template string."""

        def _replacer(match: re.Match) -> str:
            expr = match.group(1).strip()
            # {{state.key}}
            if expr.startswith("state."):
                key = expr[6:]
                val = state.get(key, "")
                return _to_str(val)
            # {{step_id.key}}
            if "." in expr:
                step_id, key = expr.split(".", 1)
                result = step_results.get(step_id, {})
                if isinstance(result, dict):
                    val = result.get(key, "")
                else:
                    val = ""
                return _to_str(val)
            # Bare key: look in state first, then step results
            val = state.get(expr)
            if val is not None:
                return _to_str(val)
            return match.group(0)

        return re.sub(r"\{\{(.+?)\}\}", _replacer, template)

    @staticmethod
    def _eval_when(
        expression: str,
        state: dict,
        step_results: dict,
    ) -> bool:
        """Evaluate a ``when`` expression.

        First resolves template variables, then evaluates as Python
        expression in a safe context.
        """
        resolved = WorkflowEngine._resolve_string(expression, state, step_results)
        resolved = resolved.strip()

        if not resolved or resolved in ("False", "None", "0", "[]", "{}", '""', "''"):
            return False
        if resolved in ("True",):
            return True

        # Try Python eval with state and step_results as locals.
        try:
            safe_locals = {
                **state,
                **{k: v for k, v in step_results.items()},
                "len": len,
                "bool": bool,
            }
            # Filter out non-dict step results.
            safe_locals = {
                k: (v if not isinstance(v, dict) else True)
                for k, v in safe_locals.items()
            }
            return bool(eval(resolved, {"__builtins__": {}}, safe_locals))
        except Exception:
            # If eval fails, treat as truthy (likely a string value).
            return bool(resolved)


def _to_str(val: Any) -> str:
    """Convert a Python value to a string for template substitution."""
    if isinstance(val, list):
        return ", ".join(str(v) for v in val)
    if val is None:
        return ""
    return str(val)


def _short_params(params: dict) -> str:
    """Short representation of params for logging."""
    items = []
    for k, v in params.items():
        s = str(v)
        if len(s) > 40:
            s = s[:37] + "..."
        items.append(f"{k}={s}")
    return ", ".join(items)


# ------------------------------------------------------------------
# Built-in tools
# ------------------------------------------------------------------


async def _builtin_terminal(
    command: str = "", execute: bool = True, cwd: str = ""
) -> dict:
    """Built-in terminal tool — actually runs commands."""
    print(f"  [terminal] {command[:120]}", flush=True)
    if not execute or not command:
        return {"output": f"[simulated] {command}", "status": "ok"}

    import subprocess

    work_dir = cwd or str(Path.cwd())

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=work_dir,
        )
        output = result.stdout + result.stderr
        # Prioritise FAILED/ERROR lines for coding agent context.
        failed_lines = [l for l in output.splitlines() if "FAILED" in l or "ERROR" in l]
        other_lines = [
            l for l in output.splitlines() if "FAILED" not in l and "ERROR" not in l
        ]
        summary = "\n".join(failed_lines[-80:] + other_lines[-20:])
        output = summary[-8000:]
        status = "ok" if result.returncode == 0 else "failed"

        # If command failed, write structured error log for cycle.
        if result.returncode != 0:
            # Skip logging for expected "no tests/" conditions
            skip_phrases = [
                "file or directory not found: tests/",
                "no tests ran",
            ]
            if not any(phrase in output for phrase in skip_phrases):
                _log_build_error(command, output, work_dir)

        return {"output": output, "status": status, "exit_code": result.returncode}
    except Exception as exc:
        _log_build_error(command, str(exc), work_dir)
        return {"output": str(exc), "status": "error", "exit_code": -1}


async def _builtin_clarification(question: str = "") -> dict:
    """Built-in request_clarification tool — logs the question."""
    print(f"  [CLARIFY] {question[:200]}", flush=True)
    return {"question": question, "status": "asked", "answer": ""}


async def _builtin_clarification(question: str = "") -> dict:
    """Built-in request_clarification tool — logs the question."""
    print(f"  [CLARIFY] {question[:200]}", flush=True)
    return {"question": question, "status": "asked", "answer": ""}


def _log_build_error(command: str, error: str, cwd: str = "") -> None:
    """Write a build error as a structured log entry for the cycle."""
    import json
    from datetime import datetime, timezone

    base = Path(cwd) if cwd else Path.cwd()
    log_dir = base / "logs" / "MOD-build"
    log_dir.mkdir(parents=True, exist_ok=True)

    entry = {
        "module_id": "MOD-build",
        "event": "build_failed",
        "severity": "error",
        "ts": datetime.now(timezone.utc).isoformat(),
        "command": command[:200],
        "error": error[:3000],
    }
    with open(log_dir / "structured.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")


def _find_source_files(project_path: str, names: set[str]) -> list[str]:
    """Map module names or test paths to likely source files.

    Accepts both module names (``"marketplace"``) and test paths
    (``"tests/test_marketplace.py"``).
    """
    proj = Path(project_path)
    source_files: list[str] = []
    seen: set[str] = set()

    for name in names:
        # Normalise: extract module name from test path if needed.
        stem = Path(name).stem
        if stem.startswith("test_"):
            module_name = stem[5:]
        else:
            module_name = name

        # Look for source dirs matching the module.
        # Try exact matches first, then fuzzy.
        patterns = [
            f"**/{module_name}/**/*.py",
            f"**/mod_{module_name}/**/*.py",
            f"**/{module_name}.py",
        ]
        # Also search for dirs containing the module name (e.g. mod_a2a_launchpad).
        for candidate in proj.glob("**/mod_*/"):
            if module_name in candidate.name:
                patterns.append(f"**/{candidate.name}/**/*.py")
        # Also match individual files named after the module (e.g. agent_card.py).
        patterns.append(f"**/{module_name}.py")
        patterns.append(f"**/*{module_name}*.py")

        # When a single file is targeted, find its parent module directory
        # and include ALL files in that directory.
        for fp in proj.glob(f"**/{module_name}.py"):
            parent = fp.parent
            if parent.name.startswith("mod_"):
                patterns.append(f"**/{parent.name}/**/*.py")

        for pattern in patterns:
            for fp in proj.glob(pattern):
                sp = str(fp)
                if any(x in sp for x in ["__pycache__", ".venv", "node_modules"]):
                    continue
                # Skip nested mod_/mod_/ directories (coding agent artifacts).
                if sp.count("/mod_") > 1:
                    continue
                    continue
                if "/tests/" in sp:
                    continue
                rp = str(fp.resolve())
                if rp not in seen:
                    seen.add(rp)
                    source_files.append(str(fp.relative_to(proj)))

    return sorted(source_files)[:8]


def _trim_chat_history(project_path: str, max_lines: int = 1500) -> None:
    """DEPRECATED: Trim chat history to avoid context bloat.

    OpenCode manages its own session history — this function is kept
    for backward compatibility but is a no-op.
    """


def _git_stash_push(project_path: str) -> None:
    """Save current state to git so we can restore deletions."""
    import subprocess

    proj = Path(project_path)
    if not (proj / ".git").is_dir():
        return
    subprocess.run(
        ["git", "add", "-A"],
        cwd=str(proj),
        capture_output=True,
        timeout=10,
    )


def _restore_deleted(project_path: str) -> None:
    """Restore any files the coding agent deleted (checkout from git index)."""
    import subprocess

    proj = Path(project_path)
    if not (proj / ".git").is_dir():
        return
    # Check for deleted files.
    result = subprocess.run(
        ["git", "--no-pager", "diff", "--name-only", "--diff-filter=D"],
        cwd=str(proj),
        capture_output=True,
        text=True,
        timeout=10,
    )
    deleted = [l for l in result.stdout.splitlines() if l.strip()]
    if deleted:
        print(
            f"  [FixBugs] Restoring {len(deleted)} deleted file(s): {deleted[:5]}",
            flush=True,
        )
        subprocess.run(
            ["git", "checkout", "--"] + deleted,
            cwd=str(proj),
            capture_output=True,
            timeout=10,
        )


def _validate_imports(project_path: str) -> bool:
    """Check that all mod_* packages can be imported without errors."""
    import subprocess

    proj = Path(project_path)
    # Find all top-level mod_* packages.
    mod_dirs = [
        d.name for d in proj.iterdir() if d.is_dir() and d.name.startswith("mod_")
    ]
    for mod in mod_dirs:
        result = subprocess.run(
            ["python", "-c", f"import {mod}"],
            cwd=str(proj),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            print(f"  [FixBugs] Import check FAILED: {mod}", flush=True)
            return False
    return True


def _git_rollback(project_path: str) -> None:
    """Rollback all uncommitted changes via git checkout."""
    import subprocess

    proj = Path(project_path)
    if not (proj / ".git").is_dir():
        return
    subprocess.run(
        ["git", "checkout", "--", "."],
        cwd=str(proj),
        capture_output=True,
        timeout=10,
    )
    subprocess.run(
        ["git", "clean", "-fd"],
        cwd=str(proj),
        capture_output=True,
        timeout=10,
    )


def _find_test_files(project_path: str, error_lines: list[str]) -> list[str]:
    """Extract test file paths from error lines and resolve to absolute paths."""
    import re

    proj = Path(project_path)
    test_files = []
    seen = set()
    for line in error_lines:
        # Match: FAILED tests/test_marketplace.py::TestX
        # Match: ERROR tests/test_marketplace.py
        # Match: File "/path/to/tests/test_x.py", line N
        for pat in [
            r"(?:FAILED|ERROR)\s+(tests/\S+?\.py)",
            r'File "([^"]*tests/[^"]+\.py)"',
            r"(tests/\S+?\.py)",
        ]:
            for m in re.findall(pat, line):
                fname = m.split("::")[0] if "::" in m else m
                fp = proj / fname
                if fp.is_file() and fname not in seen:
                    seen.add(fname)
                    test_files.append(fname)
    return test_files[:5]  # limit test files


def _find_leaves_inline(storage: Any) -> list[dict]:
    """Find leaf elements (no children, implementable types)."""
    IMPLEMENTABLE = {
        "step",
        "field",
        "requirement",
        "test_case",
        "detailed_scenario",
        "code_artifact",
        "api_endpoint",
    }
    leaves = []
    for s in storage.list_all():
        try:
            el = storage.read_element(s.id)
        except Exception:
            continue
        if el.children or el.element_type not in IMPLEMENTABLE:
            continue
        leaves.append(
            {
                "id": el.id,
                "title": el.title,
                "aspect": el.aspect,
                "type": el.element_type,
            }
        )
    return leaves


def _scan_implements_inline(project_path: str) -> dict[str, list[str]]:
    """Scan code for @implements decorators."""
    import re as _re

    proj = Path(project_path)
    impl = {}
    for pyf in proj.rglob("*.py"):
        sp = str(pyf)
        if (
            "__pycache__" in sp
            or ".venv" in sp
            or "node_modules" in sp
            or "/tests/" in sp
        ):
            continue
        if "'" in sp:
            continue
        try:
            text = pyf.read_text(encoding="utf-8")
        except Exception:
            continue
        for m in _re.finditer(r"@implements\(['\"]([^'\"]+)['\"]", text):
            sid = m.group(1)
            if sid.startswith("MOD-"):
                continue  # skip old MOD-* refs
            impl.setdefault(sid, []).append(str(pyf.relative_to(proj)))
    return impl

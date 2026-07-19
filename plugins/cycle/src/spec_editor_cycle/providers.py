"""Coding Agent Provider — single OpenCode provider for code generation.

Usage::

    from spec_editor_cycle.providers import get_provider

    provider = get_provider("opencode", "/path/to/project")
    result = await provider.run(storage=storage, task="Fix bug X")
    # → {"status": "ok", "files_changed": [...]}
"""

from __future__ import annotations

import abc
import json as _json
import os
import subprocess
from pathlib import Path
from typing import Any

from src.tracing import StructuredLogEmitter

# Resolved at import time: VSCode extension env var → hardcoded default
_DEFAULT_REASONING_MODEL = os.environ.get(
    "SPEC_EDITOR__AGENT_1__MODEL", "deepseek/deepseek-reasoner"
)


class CodingAgentProvider(abc.ABC):
    """Abstract coding agent that can fix bugs from a spec and task."""

    @abc.abstractmethod
    async def run(
        self,
        storage: Any,
        task: str,
        model: str = _DEFAULT_REASONING_MODEL,
    ) -> dict:
        """Fix code according to the task and spec.

        Args:
            storage: StorageAdapter for reading spec elements.
            task: Natural-language task with error details.
            model: LLM model identifier.

        Returns:
            dict with ``status``, ``files_changed``, ``output``, ``errors``.
        """
        ...

    @abc.abstractmethod
    def shutdown(self) -> None:
        """Release resources (persistent sessions, subprocesses)."""
        ...


# ------------------------------------------------------------------
# OpenCode Provider (the only implementation)
# ------------------------------------------------------------------


class OpenCodeProvider(CodingAgentProvider):
    """Uses OpenCode (anomalyco/opencode) for coding via ``opencode run``.

    OpenCode manages its own context: walking the file tree, building
    a repo map, selecting relevant files, and applying edits.
    """

    def __init__(self, project_path: str | Path) -> None:
        self._project_path = Path(project_path)
        self._session_id: str = ""
        self._log = StructuredLogEmitter(
            module_id="MOD-coding-agent",
            scenario_id="SCN-code-fix",
            log_dir=str(self._project_path / "logs"),
            auto_element=False,
        )

    async def run(
        self,
        storage: Any,
        task: str,
        model: str = _DEFAULT_REASONING_MODEL,
    ) -> dict:
        """Run OpenCode with a task message.

        First call starts a new session.  Subsequent calls use
        ``--continue`` to persist context and files.
        """
        self._log.info("opencode_started", task=task[:120], model=model)

        # Guard: reject empty tasks — they waste LLM calls and corrupt context file
        if not task or not task.strip():
            self._log.warning("opencode_empty_task", task=task)
            return {"status": "failed", "error": "empty task — no instruction to generate code", "files_changed": []}

        try:
            # Build spec context.
            from spec_editor_cycle.coding_agent import build_spec_context

            spec_context = build_spec_context(storage, task)

            # Append Implementation Architect decisions from linked IMP elements.
            arch_context = self._build_architect_context(storage, task)
            if arch_context:
                spec_context += "\n\n---\n\n" + arch_context

            # Load coding skill prompt and prepend to context.
            skill_prompt = self._load_skill_prompt()
            full_context = (
                skill_prompt + "\n\n---\n\n" + spec_context
                if skill_prompt
                else spec_context
            )

            context_file = self._project_path / ".opencode_spec_context.md"
            context_file.write_text(full_context, encoding="utf-8")

            # Ensure API key — fail fast with a clear error if missing.
            if not self._ensure_api_key():
                return {
                    "status": "failed",
                    "error": "No DEEPSEEK_API_KEY found. Create a .env file with DEEPSEEK_API_KEY=<your-key> in the project root.",
                    "files_changed": [],
                }

            # Build command.
            # NOTE: OpenCode internally runs "git add prompt3/.opencode_spec_context.md"
            # using the cwd as the repo root. To make this work, we run opencode from
            # the PARENT directory so the relative path "prompt3/.opencode..." resolves.
            project_name = self._project_path.name
            parent_dir = str(self._project_path.parent)

            cmd = [
                "opencode",
                "run",
                task,
                "--dir",
                project_name,
                "--format",
                "json",
                "--dangerously-skip-permissions",
                "--agent",
                "build",
                "--file",
                ".opencode_spec_context.md",
            ]

            if model and "/" in model:
                cmd.extend(["--model", model])

            if self._session_id:
                # Continue existing session for warm context.
                cmd.extend(["--continue", "--session", self._session_id])

            self._log.info("opencode_invoking", cmd=" ".join(cmd[:6]))

            result = subprocess.run(
                cmd,
                cwd=parent_dir,
                capture_output=True,
                text=True,
                timeout=600,
                env={**os.environ, "OPENCODE_LOG_LEVEL": "WARN"},
            )

            output = result.stdout
            errors = result.stderr
            self._log.info(
                "opencode_raw",
                stdout_len=len(output),
                stderr_len=len(errors),
                rc=result.returncode,
                stdout_head=output[:300] if output else "(empty)",
            )

            # Parse JSON output to extract session id and changed files.
            files_changed = self._parse_output(output)
            self._session_id = self._parse_session_id(output) or self._session_id

            self._log.info(
                "opencode_done",
                files_changed=files_changed,
                session=self._session_id[:20],
                output_len=len(output),
                errors_len=len(errors),
            )

            return {
                "status": "ok" if result.returncode == 0 else "failed",
                "files_changed": files_changed,
                "output": output[-2000:],
                "errors": errors[:500] if errors else "",
            }

        except Exception as exc:
            self._log.error("opencode_error", error=str(exc))
            return {"status": "error", "error": str(exc), "files_changed": []}

    def shutdown(self) -> None:
        self._session_id = ""

    # ------------------------------------------------------------------
    # Skill prompt loading
    # ------------------------------------------------------------------

    def _load_skill_prompt(self) -> str:
        """Load the coding agent skill prompt from skills/coding.yaml.

        Returns the prompt text with PRINCIPLES + WORKFLOW + CRITICAL RULES,
        or empty string if the skill file is not found.
        """
        skill_file = self._project_path / "skills" / "coding.yaml"
        if not skill_file.exists():
            return ""

        try:
            import yaml

            data = yaml.safe_load(skill_file.read_text()) or {}
            for skill_data in data.get("skills", []):
                if skill_data.get("name") == "coding_agent":
                    prompt = skill_data.get("prompt", "")
                    if prompt:
                        return (
                            "# Coding Agent Instructions\n\n"
                            "Follow these principles and workflow strictly:\n\n"
                            + prompt
                        )
        except Exception:
            pass
        return ""

    # ------------------------------------------------------------------
    # Architect context enrichment
    # ------------------------------------------------------------------

    def _build_architect_context(self, storage: Any, task: str) -> str:
        """Build Implementation Architect decision context from IMP-* elements.

        Searches for IMP elements linked to the modules referenced in the task,
        extracts their implementation_architect decisions, and formats them
        as immutable constraints for the coding agent.
        """
        try:
            from src.implementation.engine import ImplementationEngine

            impl_engine = ImplementationEngine(str(self._project_path))

            # Find element IDs mentioned in the task
            import re

            element_ids = set(re.findall(r'(?:MOD|ENT|NFR|IMP)-\d+(?:-\d+)?', task))

            lines: list[str] = []
            lines.append("## Implementation Architect Decisions (DO NOT CHANGE)")
            lines.append("")

            found_any = False
            for eid in sorted(element_ids):
                try:
                    element = storage.read_element(eid)
                except Exception:
                    continue

                # Find linked IMP elements
                imp_element = None
                for child_id in getattr(element, "children", []) or []:
                    if child_id.startswith("IMP-"):
                        try:
                            imp_element = storage.read_element(child_id)
                            break
                        except Exception:
                            pass

                if imp_element is None:
                    # Search IMP elements by relationships
                    for imp_summary in storage.list_all():
                        if not imp_summary.id.startswith("IMP-"):
                            continue
                        try:
                            imp = storage.read_element(imp_summary.id)
                            rels = imp.relationships or {}
                            for entries in rels.values():
                                if any(
                                    getattr(e, "target", "") == eid for e in entries
                                ):
                                    imp_element = imp
                                    break
                            if imp_element:
                                break
                        except Exception:
                            pass

                if imp_element is None:
                    continue

                ia = getattr(imp_element, "implementation_architect", None)
                if not ia:
                    continue

                found_any = True
                lines.append(f"### {eid} (plan: {imp_element.id})")
                lines.append("")
                lines.append("| Decision | Value |")
                lines.append("|----------|-------|")

                label_map = {
                    "structure": "Structural pattern",
                    "domain_style": "Domain style",
                    "ddd_type": "DDD classification",
                    "template": "Code template",
                    "layer": "Target layer",
                }
                for key, label in label_map.items():
                    if key in ia:
                        lines.append(f"| {label} | `{ia[key]}` |")

                if ia.get("ports"):
                    lines.append(
                        f"| Required ports | {', '.join(f'`{p}`' for p in ia['ports'])} |"
                    )
                if ia.get("adapters"):
                    lines.append(
                        f"| Required adapters | {', '.join(f'`{a}`' for a in ia['adapters'])} |"
                    )
                lines.append("")

            if not found_any:
                return ""

            lines.append(
                "These decisions were made by the Implementation Architect. "
                "Follow them exactly. Do not choose different patterns or layers."
            )
            return "\n".join(lines)

        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ensure_api_key(self) -> bool:
        """Ensure DEEPSEEK_API_KEY is in the environment.

        Returns True if a key was found or already set, False otherwise.
        Uses Secrets Provider for resolution, falling back to .env file parsing.
        """
        if "DEEPSEEK_API_KEY" in os.environ:
            return True

        # Try Secrets Provider first
        try:
            from src.secrets import create_secret_provider

            secrets = create_secret_provider(str(self._project_path))
            api_key = secrets.get_secret("DEEPSEEK_API_KEY")
            if api_key:
                os.environ["DEEPSEEK_API_KEY"] = api_key
                return True
        except Exception:
            pass

        # Fallback: parse .env file
        env_file = self._project_path / ".env"
        if not env_file.exists():
            env_file = Path(__file__).resolve().parent.parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip()
            if "DEEPSEEK_API_KEY" in os.environ:
                return True

        # --- CRITICAL: no API key found ---
        self._log.error(
            "opencode_no_api_key",
            project=str(self._project_path),
            hint="Create a .env file with DEEPSEEK_API_KEY=<your-key> in the project root.",
        )
        return False

    @staticmethod
    def _parse_output(output: str) -> list[str]:
        """Extract changed file paths from OpenCode JSON output.

        OpenCode reports edits via tool_use events with tool="edit" or
        tool="write", storing the filePath in part.state.input.
        """
        files = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            if event.get("type") != "tool_use":
                continue
            part = event.get("part", {})
            tool = part.get("tool", "")
            if tool not in ("edit", "write"):
                continue
            state = part.get("state", {})
            inp = state.get("input", {})
            path = inp.get("filePath", "") or inp.get("file_path", "")
            if path:
                files.append(path)
        return sorted(set(files))

    @staticmethod
    def _parse_session_id(output: str) -> str:
        """Extract session ID from OpenCode output."""
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            sid = event.get("sessionID", "") or event.get("session_id", "")
            if sid:
                return sid
        return ""


# ------------------------------------------------------------------
# Factory
# ------------------------------------------------------------------


def get_provider(name: str, project_path: str | Path) -> CodingAgentProvider:
    """Get a coding agent provider by name.

    Args:
        name: ``"opencode"`` or ``"litellm"``.
        project_path: Path to the project.

    Returns:
        A :class:`CodingAgentProvider` instance.
    """
    if name == "opencode":
        return OpenCodeProvider(project_path)
    if name == "litellm":
        return LitellmProvider(project_path)
    raise ValueError(f"Unknown provider: {name}. Available: opencode, litellm")


# ------------------------------------------------------------------
# Litellm Provider (direct LLM API, no OpenCode dependency)
# ------------------------------------------------------------------


class LitellmProvider(CodingAgentProvider):
    """Uses litellm to call LLM API directly for code generation.

    No external dependency (OpenCode). Generates code by sending
    spec + task to the LLM and writing the response as files.
    """

    def __init__(self, project_path: str | Path) -> None:
        import structlog

        self._project_path = Path(project_path)
        self._log = structlog.get_logger(__name__).bind(
            module="MOD-coding-agent"
        )
        self._session_id: str = ""

    def shutdown(self) -> None:
        """No persistent state to release."""
        pass

    async def run(
        self,
        storage: Any,
        task: str,
        model: str = _DEFAULT_REASONING_MODEL,
    ) -> dict:
        """Generate code by calling LLM API directly."""
        import os
        import re
        import subprocess

        self._log.info("litellm_started", task=task[:120], model=model)

        if not task or not task.strip():
            self._log.warning("litellm_empty_task")
            return {
                "status": "failed",
                "error": "empty task",
                "files_changed": [],
            }

        # Ensure API key
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            env_file = self._project_path / ".env"
            if env_file.exists():
                for line in env_file.read_text().splitlines():
                    if "DEEPSEEK_API_KEY" in line and "=" in line:
                        api_key = line.split("=", 1)[1].strip()
                        os.environ["DEEPSEEK_API_KEY"] = api_key
                        break
        if not api_key:
            self._log.error("litellm_no_api_key")
            return {
                "status": "failed",
                "error": "No DEEPSEEK_API_KEY found. Create .env file.",
                "files_changed": [],
            }

        try:
            import litellm

            # Build prompt
            src_list = ""
            src_dir = self._project_path / "src"
            if src_dir.exists():
                py_files = sorted(src_dir.rglob("*.py"))[:30]
                src_list = "\n".join(f"  {f.relative_to(self._project_path)}" for f in py_files)

            prompt = (
                "You are a coding agent. Generate Python code based on this task.\n\n"
                f"**Project root**: {self._project_path}\n\n"
                f"**Existing source files**:\n{src_list}\n\n"
                f"**Task**:\n{task[:4000]}\n\n"
                "Output ONLY code files in this format:\n\n"
                "===FILE: path/to/file.py===\n"
                "<code here>\n"
                "===END===\n\n"
                "Write complete, working Python code with proper typing and docstrings."
            )

            response = litellm.completion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                api_key=api_key,
                temperature=0.1,
                max_tokens=4000,
                timeout=300,
            )
            text = response.choices[0].message.content or ""

            # Parse response for files
            pattern = re.compile(r"===FILE:\s*(.+?)===\s*\n(.*?)===END===", re.DOTALL)
            files_written = []
            for match in pattern.finditer(text):
                fpath = match.group(1).strip()
                content = match.group(2).strip()
                # Strip leading src/ since project_path is root
                fpath = re.sub(r'^src/', '', fpath)
                full_path = self._project_path / fpath
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(content)
                files_written.append(fpath)

            self._log.info("litellm_done", files=len(files_written))
            return {
                "status": "ok" if files_written else "failed",
                "files_changed": files_written,
                "output": text[:1000],
                "errors": "" if files_written else "No files parsed from LLM response",
            }

        except Exception as exc:
            self._log.error("litellm_failed", error=str(exc))
            return {
                "status": "failed",
                "error": str(exc)[:500],
                "files_changed": [],
            }

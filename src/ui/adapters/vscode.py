"""VSCode editor adapter.

Implements IEditorAdapter for VSCode extension integration.
When the VSCode extension starts the MCP server, it injects this
adapter to provide editor-specific functionality.

References:
    SRC-UI-ADAPTER: EditorAdapter for VSCode/Zed/standalone integration
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from src.ui.adapters.base import (
    Disposable,
    GitCommit,
    IEditorAdapter,
    ProjectInfo,
    SCMFileState,
)


class VscodeAdapter(IEditorAdapter):
    """Editor adapter for VSCode extension.

    When running inside VSCode, the extension spawns the MCP server
    with environment variables pointing to the workspace. This adapter
    reads those env vars and uses the filesystem directly.
    """

    def __init__(self) -> None:
        self._workspace_root = os.environ.get("SPEC_EDITOR_WORKSPACE")
        self._project_path = os.environ.get("SPEC_EDITOR_PROJECT")

    # ── Identity ──────────────────────────────────────────────────────

    def editor_name(self) -> str:
        return "vscode"

    def editor_version(self) -> str:
        return os.environ.get("SPEC_EDITOR_EDITOR_VERSION", "0.0.0")

    # ── Project discovery ──────────────────────────────────────────────

    def find_projects(self, base_dir: Path | None = None) -> list[ProjectInfo]:
        projects: list[ProjectInfo] = []
        search_root = base_dir or (
            Path(self._workspace_root) if self._workspace_root else Path.home()
        )

        if not search_root.exists():
            return projects

        for marker in search_root.rglob("methodology.yaml"):
            proj_dir = marker.parent
            try:
                methodology = _read_yaml_simple(marker)
                element_count = sum(
                    1
                    for _ in (proj_dir / "aspects").rglob("*.md")
                    if _.name != "README.md"
                )
                projects.append(
                    ProjectInfo(
                        path=proj_dir,
                        name=methodology.get("name", proj_dir.name),
                        methodology=methodology.get("methodology", "unknown"),
                        element_count=element_count,
                    )
                )
            except Exception:
                pass

        return projects

    def get_current_project(self) -> Path | None:
        if self._project_path:
            p = Path(self._project_path)
            if (p / "methodology.yaml").exists():
                return p
        if self._workspace_root:
            p = Path(self._workspace_root)
            if (p / "methodology.yaml").exists():
                return p
        return None

    def set_current_project(self, path: Path) -> None:
        if not (path / "methodology.yaml").exists():
            raise ValueError(f"Not a spec-editor project: {path}")
        self._project_path = str(path)

    def on_project_changed(self, callback) -> Disposable:
        # VSCode workspace changes are handled by the extension, not the server
        return Disposable(lambda: None)

    # ── File system ───────────────────────────────────────────────────

    def read_file(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def write_file(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def delete_file(self, path: Path) -> None:
        if path.exists():
            path.unlink()

    def list_directory(self, path: Path) -> list[str]:
        return [p.name for p in path.iterdir()] if path.exists() else []

    def walk_directory(self, path: Path) -> list[Path]:
        if not path.exists():
            return []
        return [p.relative_to(path) for p in path.rglob("*") if p.is_file()]

    def watch_directory(self, path, callback) -> Disposable:
        # File watching is handled by VSCode's createFileSystemWatcher
        return Disposable(lambda: None)

    # ── Git ───────────────────────────────────────────────────────────

    def git_history(self, path: Path, max_count: int = 50) -> list[GitCommit]:
        return _git_log(path, max_count)

    def git_diff(self, path: Path) -> str:
        return _git_command(["diff", "--", str(path)], cwd=path.parent)

    def git_branches(self) -> list[str]:
        root = self._git_root()
        if not root:
            return []
        output = _git_command(["branch", "--format=%(refname:short)"], cwd=root)
        return [b.strip() for b in output.split("\n") if b.strip()]

    def git_checkout(self, branch: str) -> None:
        root = self._git_root()
        if root:
            _git_command(["checkout", branch], cwd=root)

    # ── UI ────────────────────────────────────────────────────────────

    def show_info(self, message: str) -> None:
        print(f"[spec-editor] {message}")

    def show_warning(self, message: str) -> None:
        print(f"[spec-editor] ⚠ {message}")

    def show_error(self, message: str) -> None:
        print(f"[spec-editor] ❌ {message}")

    def pick_folder(self, title: str = "Select folder") -> Path | None:
        return None

    def pick_file(
        self, title: str = "Select file", filters: dict[str, list[str]] | None = None
    ) -> Path | None:
        return None

    # ── Config ────────────────────────────────────────────────────────

    def get_config(self, key: str, default: Any = None) -> Any:
        return os.environ.get(f"SPEC_EDITOR_{key.upper().replace('.', '_')}", default)

    def set_config(self, key: str, value: Any) -> None:
        os.environ[f"SPEC_EDITOR_{key.upper().replace('.', '_')}"] = str(value)

    # ── Secrets ───────────────────────────────────────────────────────

    def get_secret(self, key: str) -> str | None:
        return os.environ.get(f"SPEC_EDITOR_SECRET_{key.upper()}")

    def set_secret(self, key: str, value: str) -> None:
        os.environ[f"SPEC_EDITOR_SECRET_{key.upper()}"] = value

    def delete_secret(self, key: str) -> None:
        os.environ.pop(f"SPEC_EDITOR_SECRET_{key.upper()}", None)

    # ── Internal ──────────────────────────────────────────────────────

    def _git_root(self) -> Path | None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=self._project_path or self._workspace_root or Path.cwd(),
            )
            if result.returncode == 0:
                return Path(result.stdout.strip())
        except Exception:
            pass
        return None


# ==========================================================================
# Helpers
# ==========================================================================


def _read_yaml_simple(path: Path) -> dict[str, Any]:
    """Read a simple YAML file without requiring PyYAML."""
    import re

    text = path.read_text(encoding="utf-8")
    result: dict[str, Any] = {}
    for line in text.split("\n"):
        m = re.match(r"^(\w[\w_]*):\s*(.*)$", line.strip())
        if m:
            key, val = m.group(1), m.group(2).strip()
            result[key] = val.strip("'\"")
    return result


def _git_command(args: list[str], cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=cwd,
        )
        return result.stdout
    except Exception:
        return ""


def _git_log(path: Path, max_count: int) -> list[GitCommit]:
    try:
        result = subprocess.run(
            [
                "git",
                "--no-pager",
                "log",
                f"-n{max_count}",
                "--format=%H|%an|%aI|%s",
                "--",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=path.parent,
        )
        commits = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append(
                    GitCommit(
                        hash=parts[0],
                        author=parts[1],
                        date=parts[2],
                        message=parts[3],
                    )
                )
        return commits
    except Exception:
        return []

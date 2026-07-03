"""Standalone adapter — CLI/standalone mode.

Uses:
- ``.spec-project`` marker file for project discovery
- ``git`` CLI for version control
- Direct filesystem access
- Console output for UI messages
- ``.env`` file for configuration

References:
    SRC-UI-ADAPTER: EditorAdapter for VSCode/Zed/standalone integration
"""

from __future__ import annotations

import contextlib
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from src.ui.adapters.base import (
    Disposable,
    GitCommit,
    IEditorAdapter,
    ProjectInfo,
)

# =============================================================================
# Marker file — used for project discovery in standalone mode.
# NOT local.yaml — that file holds user settings (queue_url, etc.)
# and must not be overwritten.
# =============================================================================

_LOCAL_MARKER = "local.yaml"


# =============================================================================
# Standalone Adapter
# =============================================================================


class StandaloneAdapter(IEditorAdapter):
    """Editor adapter for standalone/CLI mode.

    Provides the same interface as editor extensions, but all operations
    are done locally: file system, ``git`` CLI, console output, ``.env`` config.
    """

    def __init__(self) -> None:
        self._project_listeners: list[Callable[[Path | None], None]] = []
        self._current_project: Path | None = None

    # ── Version / identity ─────────────────────────────────────────────

    def editor_name(self) -> str:
        return "standalone"

    def editor_version(self) -> str:
        return "0.0.0"

    # ── Project discovery and switching ─────────────────────────────────

    def find_projects(self, base_dir: Path | None = None) -> list[ProjectInfo]:
        """Find all spec-editor projects under ``base_dir``.

        Searches for directories containing ``methodology.yaml``.
        If ``base_dir`` is None, searches in ``$HOME/Projects``, ``$HOME``,
        and the current working directory.
        """
        from src.config.methodology import load_methodology

        candidates: list[Path] = []

        if base_dir is not None:
            bases = [base_dir]
        else:
            # Default search paths
            home = Path.home()
            bases = [
                Path.cwd(),
                home / "Projects",
                home,
            ]

        for base in bases:
            if not base.is_dir():
                continue
            # Search up to 3 levels deep
            for root, dirs in _walk_dirs(base, max_depth=3):
                for d in dirs:
                    maybe = root / d / "methodology.yaml"
                    if maybe.is_file():
                        candidates.append(root / d)

        result: list[ProjectInfo] = []
        seen: set[str] = set()
        for cand in candidates:
            key = str(cand.resolve())
            if key in seen:
                continue
            seen.add(key)
            info = ProjectInfo(path=cand)
            try:
                method = load_methodology(cand / "methodology.yaml")
                info.methodology = method.name
            except Exception:
                pass
            # Count elements
            aspects = cand / "aspects"
            if aspects.is_dir():
                info.element_count = len(list(aspects.rglob("*.md")))
            result.append(info)

        return sorted(result, key=lambda p: p.name.lower())

    def get_current_project(self) -> Path | None:
        # 1. Check cached value
        if self._current_project and self._current_project.is_dir():
            return self._current_project

        # 2. Check SPEC_EDITOR_PROJECT env var
        import os

        env_project = os.environ.get("SPEC_EDITOR_PROJECT")
        if env_project:
            env_path = Path(env_project)
            if env_path.is_dir():
                self._current_project = env_path
                return env_path

        # 3. Search for local.yaml marker up the directory tree
        candidate = _find_marker_upwards(Path.cwd(), _LOCAL_MARKER)
        if candidate:
            self._current_project = candidate
            return candidate

        # 4. Search for methodology.yaml in CWD
        cwd = Path.cwd()
        if (cwd / "methodology.yaml").is_file():
            self._current_project = cwd
            return cwd

        return None

    def set_current_project(self, path: Path) -> None:
        if not path.is_dir():
            raise ValueError(f"Not a directory: {path}")
        if not (path / "methodology.yaml").is_file():
            raise ValueError(
                f"Not a spec-editor project: {path} "
                f"(missing methodology.yaml). Run 'spec-editor init'."
            )
        # In-memory only — do NOT write to local.yaml.
        # local.yaml holds user settings (queue_url, etc.) and must not
        # be modified by the MCP server.
        self._current_project = path

        # Notify listeners
        for listener in self._project_listeners:
            with contextlib.suppress(Exception):
                listener(path)

    def on_project_changed(self, callback: Callable[[Path | None], None]) -> Disposable:
        self._project_listeners.append(callback)
        return Disposable(lambda: self._project_listeners.remove(callback))

    # ── File system ─────────────────────────────────────────────────────

    def read_file(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def write_file(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def delete_file(self, path: Path) -> None:
        path.unlink(missing_ok=True)

    def list_directory(self, path: Path) -> list[str]:
        if not path.is_dir():
            return []
        return sorted([e.name for e in path.iterdir() if not e.name.startswith(".")])

    def walk_directory(self, path: Path) -> list[Path]:
        if not path.is_dir():
            return []
        result: list[Path] = []
        for entry in path.rglob("*"):
            if entry.is_file() and not any(p.startswith(".") for p in entry.parts):
                result.append(entry.relative_to(path))
        return sorted(result)

    def watch_directory(
        self, path: Path, callback: Callable[[Path, str], None]
    ) -> Disposable:
        # Standalone mode: polling-based watcher (simplified)
        import threading

        stop_event = threading.Event()

        def _poll() -> None:
            seen: dict[str, float] = {}
            # Initial snapshot
            if path.is_dir():
                for f in path.rglob("*"):
                    if f.is_file():
                        seen[str(f)] = f.stat().st_mtime
            while not stop_event.wait(0.5):
                try:
                    _check_directory(path, seen, callback)
                except Exception:
                    break

        thread = threading.Thread(target=_poll, daemon=True)
        thread.start()

        def _stop() -> None:
            stop_event.set()
            thread.join(timeout=1.0)

        return Disposable(_stop)

    # ── Version control (Git) ───────────────────────────────────────────

    def git_history(self, path: Path, max_count: int = 50) -> list[GitCommit]:
        try:
            result = subprocess.run(
                [
                    "git",
                    "--no-pager",
                    "log",
                    f"-{max_count}",
                    "--format=%h|%an|%aI|%s",
                    "--",
                    str(path),
                ],
                capture_output=True,
                text=True,
                cwd=str(self._get_git_root(path)),
                timeout=10,
            )
            if result.returncode != 0:
                return []
            commits: list[GitCommit] = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("|", 3)
                if len(parts) >= 4:
                    commits.append(
                        GitCommit(
                            hash=parts[0],
                            author=parts[1],
                            date=parts[2],
                            message=parts[3],
                        )
                    )
            return commits
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return []

    def git_diff(self, path: Path) -> str:
        try:
            result = subprocess.run(
                ["git", "--no-pager", "diff", "--", str(path)],
                capture_output=True,
                text=True,
                cwd=str(self._get_git_root(path)),
                timeout=10,
            )
            return result.stdout if result.returncode == 0 else ""
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return ""

    def git_branches(self) -> list[str]:
        project = self.get_current_project()
        if not project:
            return []
        try:
            result = subprocess.run(
                ["git", "--no-pager", "branch"],
                capture_output=True,
                text=True,
                cwd=str(project),
                timeout=10,
            )
            if result.returncode != 0:
                return []
            branches: list[str] = []
            for line in result.stdout.strip().split("\n"):
                branch = line.lstrip("* ").strip()
                if branch:
                    branches.append(branch)
            return branches
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return []

    def git_checkout(self, branch: str) -> None:
        project = self.get_current_project()
        if not project:
            raise ValueError("No project loaded")
        subprocess.run(
            ["git", "checkout", branch],
            cwd=str(project),
            check=True,
            timeout=30,
            capture_output=True,
        )

    # ── UI ───────────────────────────────────────────────────────────────

    def show_info(self, message: str) -> None:
        print(f"[INFO] {message}")

    def show_warning(self, message: str) -> None:
        print(f"[WARN] {message}")

    def show_error(self, message: str) -> None:
        print(f"[ERROR] {message}")

    def pick_folder(self, title: str = "Select folder") -> Path | None:
        # CLI mode: return CWD or SPEC_EDITOR_PROJECT
        project = self.get_current_project()
        if project:
            return project
        return Path.cwd()

    def pick_file(
        self, title: str = "Select file", filters: dict[str, list[str]] | None = None
    ) -> Path | None:
        return None  # CLI mode doesn't have file picker

    # ── Configuration ────────────────────────────────────────────────────

    def get_config(self, key: str, default: Any = None) -> Any:
        import os

        # Map dot-separated keys to environment variables
        env_key = key.upper().replace(".", "_")
        value = os.environ.get(env_key)
        if value is not None:
            return value

        # Check .env file in current project
        project = self.get_current_project()
        if project:
            env_file = project / ".env"
            if env_file.is_file():
                for line in env_file.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k.strip() == env_key:
                        return v.strip().strip('"').strip("'")

        return default

    def set_config(self, key: str, value: Any) -> None:
        # Standalone: write to .env file in project
        project = self.get_current_project()
        if not project:
            raise ValueError("No project loaded — cannot write config")
        env_file = project / ".env"
        env_key = key.upper().replace(".", "_")

        lines: list[str] = []
        found = False
        if env_file.is_file():
            for line in env_file.read_text().splitlines():
                if line.strip().startswith(f"{env_key}="):
                    lines.append(f"{env_key}={value}")
                    found = True
                else:
                    lines.append(line)
        if not found:
            lines.append(f"{env_key}={value}")
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ── Secrets ──────────────────────────────────────────────────────────

    def get_secret(self, key: str) -> str | None:
        # Standalone: read from environment variable
        import os

        env_key = key.upper().replace(".", "_")
        return os.environ.get(env_key)

    def set_secret(self, key: str, value: str) -> None:
        # Standalone: store to .env file
        self.set_config(key, value)

    def delete_secret(self, key: str) -> None:
        # Standalone: remove from .env file
        project = self.get_current_project()
        if not project:
            return
        env_file = project / ".env"
        if not env_file.is_file():
            return
        env_key = key.upper().replace(".", "_")
        lines = [
            line
            for line in env_file.read_text().splitlines()
            if not line.strip().startswith(f"{env_key}=")
        ]
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _get_git_root(path: Path) -> Path:
        """Find the git repository root for a given path."""
        current = path.resolve()
        if current.is_file():
            current = current.parent
        for parent in [current, *current.parents]:
            if (parent / ".git").is_dir():
                return parent
        return current


# =============================================================================
# Internal helpers
# =============================================================================


def _find_marker_upwards(start: Path, marker_name: str) -> Path | None:
    """Walk up the directory tree looking for a marker file.

    The marker file is a YAML file with a ``project_path`` key pointing
    to the actual project directory.
    """
    for directory in [start, *start.parents]:
        marker = directory / marker_name
        if marker.is_file():
            try:
                data = yaml.safe_load(marker.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "project_path" in data:
                    project_path = Path(data["project_path"])
                    if project_path.is_dir():
                        return project_path
            except Exception:
                pass
    return None


def _walk_dirs(root: Path, max_depth: int = 3) -> list[tuple[Path, list[str]]]:
    """Walk directories up to ``max_depth`` levels, yielding (root, dirs) tuples."""
    result: list[tuple[Path, list[str]]] = []
    _walk_dirs_recursive(root, max_depth, result)
    return result


def _walk_dirs_recursive(
    root: Path, depth: int, result: list[tuple[Path, list[str]]]
) -> None:
    if depth < 0:
        return
    try:
        entries = list(root.iterdir())
    except PermissionError:
        return
    dirs = [e.name for e in entries if e.is_dir() and not e.name.startswith(".")]
    if dirs:
        result.append((root, dirs))
        for d in dirs:
            _walk_dirs_recursive(root / d, depth - 1, result)


def _check_directory(
    path: Path,
    seen: dict[str, float],
    callback: Callable[[Path, str], None],
) -> None:
    """Check a directory for file changes (polling)."""
    new_seen: dict[str, float] = {}
    if path.is_dir():
        for f in path.rglob("*"):
            if f.is_file():
                key = str(f)
                mtime = f.stat().st_mtime
                new_seen[key] = mtime
    # Detect changes
    for key, mtime in new_seen.items():
        if key not in seen:
            callback(Path(key), "created")
        elif seen[key] != mtime:
            callback(Path(key), "changed")
    for key in seen:
        if key not in new_seen:
            callback(Path(key), "deleted")
    seen.clear()
    seen.update(new_seen)

"""Abstract editor adapter interface.

Defines the contract between spec-editor core and the host editor/environment.
Each concrete adapter (standalone, VSCode, ZED) implements this interface.

References:
    SRC-UI-ADAPTER: EditorAdapter for VSCode/Zed/standalone integration
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# =============================================================================
# Data types
# =============================================================================


@dataclass
class GitCommit:
    """A single git commit."""

    hash: str
    author: str
    date: str
    message: str


@dataclass
class SCMFileState:
    """State of a single file in SCM (source control)."""

    path: Path
    status: str  # "modified", "added", "deleted", "untracked"


@dataclass
class ProjectInfo:
    """Discovered spec-editor project."""

    path: Path
    name: str = ""
    methodology: str = ""
    element_count: int = 0

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.path.name


class Disposable:
    """A handle that can be used to unsubscribe/unregister.

    Call ``dispose()`` to release the resource (event listener, watcher, etc.).
    """

    def __init__(self, dispose_fn: Callable[[], None]) -> None:
        self._dispose_fn = dispose_fn
        self._disposed = False

    def dispose(self) -> None:
        if not self._disposed:
            self._disposed = True
            self._dispose_fn()


# =============================================================================
# Abstract adapter interface
# =============================================================================


class IEditorAdapter(ABC):
    """Abstract interface for editor-specific functionality.

    Each method documents which editor features it maps to:
    - Standalone: CLI mode, local filesystem, git CLI
    - VSCode: vscode.workspace, vscode.scm, vscode.window, vscode.secrets
    - ZED: Worktree, MCP proxy
    """

    # ── Version / identity ─────────────────────────────────────────────

    @abstractmethod
    def editor_name(self) -> str:
        """Human-readable name of the editor/environment.

        Returns:
            "standalone", "vscode", "zed", etc.

        VSCode mapping: ``vscode.env.appName``
        ZED mapping: "zed" (hardcoded)
        """
        ...

    @abstractmethod
    def editor_version(self) -> str:
        """Version string of the editor/environment.

        Returns:
            Editor version, or "0.0.0" if standalone.

        VSCode mapping: ``vscode.version``
        ZED mapping: ``zed_extension_api::current_platform()``
        """
        ...

    # ── Project discovery and switching ──────────────────────────────────

    @abstractmethod
    def find_projects(self, base_dir: Path | None = None) -> list[ProjectInfo]:
        """Find all spec-editor projects under ``base_dir``.

        A spec-editor project is a directory containing ``methodology.yaml``
        or a ``local.yaml`` marker file.

        If ``base_dir`` is None, searches in the editor workspace or $HOME.

        VSCode mapping: ``workspace.findFiles('**/methodology.yaml')``
        ZED mapping: via MCP tool
        """
        ...

    @abstractmethod
    def get_current_project(self) -> Path | None:
        """Return the path of the currently active spec-editor project.

        In VSCode, this corresponds to the workspace folder containing
        methodology.yaml. In standalone mode, it reads from ``local.yaml``.

        Returns:
            Path to project, or None if no project is active.

        VSCode mapping: ``workspace.workspaceFolders`` + methodology.yaml check
        ZED mapping: via MCP tool
        """
        ...

    @abstractmethod
    def set_current_project(self, path: Path) -> None:
        """Persist the choice of the active project.

        In standalone mode, writes to ``local.yaml`` marker.
        In VSCode, stores in ``ExtensionContext.workspaceState``.

        Throws:
            ValueError: if the path is not a valid spec-editor project.
        """
        ...

    @abstractmethod
    def on_project_changed(self, callback: Callable[[Path | None], None]) -> Disposable:
        """Subscribe to workspace/project change events.

        The callback is invoked with the new project path (or None).

        VSCode mapping: ``workspace.onDidChangeWorkspaceFolders``
        ZED mapping: N/A (single Worktree per session)
        """
        ...

    # ── File system ─────────────────────────────────────────────────────

    @abstractmethod
    def read_file(self, path: Path) -> str:
        """Read a text file from the project.

        VSCode mapping: ``workspace.fs.readFile`` + decode
        ZED mapping: via MCP tool
        """
        ...

    @abstractmethod
    def write_file(self, path: Path, content: str) -> None:
        """Write a text file to the project (atomic, creates parent dirs).

        VSCode mapping: ``workspace.fs.writeFile`` + encode
        ZED mapping: via MCP tool
        """
        ...

    @abstractmethod
    def delete_file(self, path: Path) -> None:
        """Delete a file from the project.

        VSCode mapping: ``workspace.fs.delete``
        ZED mapping: via MCP tool
        """
        ...

    @abstractmethod
    def list_directory(self, path: Path) -> list[str]:
        """List entries in a directory (filenames only).

        VSCode mapping: ``workspace.fs.readDirectory``
        ZED mapping: via MCP tool
        """
        ...

    @abstractmethod
    def walk_directory(self, path: Path) -> list[Path]:
        """Recursively list all files in a directory.

        Returns:
            List of relative file paths.

        VSCode mapping: ``workspace.findFiles('**/*', exclude)``
        ZED mapping: via MCP tool
        """
        ...

    @abstractmethod
    def watch_directory(
        self, path: Path, callback: Callable[[Path, str], None]
    ) -> Disposable:
        """Watch a directory for file changes.

        Args:
            path: Directory to watch.
            callback: Called with ``(file_path, event_type)`` where
                      event_type is "created", "changed", or "deleted".

        VSCode mapping: ``workspace.createFileSystemWatcher``
        ZED mapping: N/A (via MCP tool)
        """
        ...

    # ── Version control (Git) ────────────────────────────────────────────

    @abstractmethod
    def git_history(self, path: Path, max_count: int = 50) -> list[GitCommit]:
        """Get git history for a file or directory.

        VSCode mapping: ``git extension`` + ``repository.log``
        ZED mapping: via MCP tool
        """
        ...

    @abstractmethod
    def git_diff(self, path: Path) -> str:
        """Get the current diff for a file (unstaged changes).

        VSCode mapping: ``git extension`` + ``repository.diff``
        ZED mapping: via MCP tool
        """
        ...

    @abstractmethod
    def git_branches(self) -> list[str]:
        """List git branches in the current project.

        VSCode mapping: ``git extension`` + ``repository.state.HEAD``
        ZED mapping: via MCP tool
        """
        ...

    @abstractmethod
    def git_checkout(self, branch: str) -> None:
        """Switch to a git branch.

        VSCode mapping: ``git extension`` + ``repository.checkout``
        ZED mapping: via MCP tool
        """
        ...

    # ── UI ────────────────────────────────────────────────────────────────

    @abstractmethod
    def show_info(self, message: str) -> None:
        """Show an informational message to the user.

        VSCode mapping: ``window.showInformationMessage``
        ZED mapping: N/A (MCP response text)
        """
        ...

    @abstractmethod
    def show_warning(self, message: str) -> None:
        """Show a warning message to the user.

        VSCode mapping: ``window.showWarningMessage``
        ZED mapping: N/A (MCP response text)
        """
        ...

    @abstractmethod
    def show_error(self, message: str) -> None:
        """Show an error message to the user.

        VSCode mapping: ``window.showErrorMessage``
        ZED mapping: N/A (MCP response text)
        """
        ...

    @abstractmethod
    def pick_folder(self, title: str = "Select folder") -> Path | None:
        """Show a folder picker dialog and return the selected path.

        VSCode mapping: ``window.showOpenDialog({ canSelectFolders: true })``
        ZED mapping: N/A (CLI argument or MCP tool)
        """
        ...

    @abstractmethod
    def pick_file(
        self, title: str = "Select file", filters: dict[str, list[str]] | None = None
    ) -> Path | None:
        """Show a file picker dialog and return the selected path.

        Args:
            title: Dialog title.
            filters: Dict of label → list of extensions, e.g.
                     ``{"Markdown": ["md"]}``.

        VSCode mapping: ``window.showOpenDialog({ filters: ... })``
        ZED mapping: N/A
        """
        ...

    # ── Configuration ────────────────────────────────────────────────────

    @abstractmethod
    def get_config(self, key: str, default: Any = None) -> Any:
        """Read editor/project configuration.

        The key is a dot-separated path, e.g. ``"specEditor.llmApiKey"``.

        VSCode mapping: ``workspace.getConfiguration('specEditor').get(key)``
        ZED mapping: N/A (file-based config)
        """
        ...

    @abstractmethod
    def set_config(self, key: str, value: Any) -> None:
        """Write editor/project configuration.

        VSCode mapping: ``workspace.getConfiguration('specEditor').update(key, value)``
        ZED mapping: N/A (file-based config)
        """
        ...

    # ── Secrets ──────────────────────────────────────────────────────────

    @abstractmethod
    def get_secret(self, key: str) -> str | None:
        """Retrieve a secret (e.g. API key) from secure storage.

        VSCode mapping: ``secrets.get(key)``
        ZED mapping: N/A (environment variable or config file)
        """
        ...

    @abstractmethod
    def set_secret(self, key: str, value: str) -> None:
        """Store a secret in secure storage.

        VSCode mapping: ``secrets.store(key, value)``
        ZED mapping: N/A
        """
        ...

    @abstractmethod
    def delete_secret(self, key: str) -> None:
        """Delete a secret from secure storage.

        VSCode mapping: ``secrets.delete(key)``
        ZED mapping: N/A
        """
        ...

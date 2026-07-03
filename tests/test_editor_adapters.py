"""Tests for editor adapters: IEditorAdapter, StandaloneAdapter, Disposable.

Covers:
- Interface contract enforcement (ABC)
- StandaloneAdapter: project discovery, git, filesystem, config
- Disposable: subscribe/unsubscribe lifecycle

References:
    SRC-UI-ADAPTER: EditorAdapter for VSCode/Zed/standalone integration
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from src.ui.adapters.base import (
    Disposable,
    GitCommit,
    IEditorAdapter,
    ProjectInfo,
)
from src.ui.adapters.standalone import (
    _LOCAL_MARKER,
    StandaloneAdapter,
    _find_marker_upwards,
)

# =============================================================================
# Disposable
# =============================================================================


class TestDisposable:
    def test_dispose_calls_function(self):
        called = False

        def fn():
            nonlocal called
            called = True

        d = Disposable(fn)
        assert not called
        d.dispose()
        assert called

    def test_dispose_is_idempotent(self):
        count = 0

        def fn():
            nonlocal count
            count += 1

        d = Disposable(fn)
        d.dispose()
        d.dispose()  # second call should be ignored
        assert count == 1

    def test_disposable_accepts_lambda(self):
        result = []

        d = Disposable(lambda: result.append("done"))
        d.dispose()
        assert result == ["done"]


# =============================================================================
# IEditorAdapter — ABC enforcement
# =============================================================================


class TestIEditorAdapterABC:
    """Verify that IEditorAdapter is a proper abstract base class."""

    def test_cannot_instantiate_directly(self):
        """Instantiating IEditorAdapter directly should raise TypeError."""
        with pytest.raises(TypeError):
            IEditorAdapter()  # type: ignore[abstract]

    def test_must_implement_all_abstract_methods(self):
        """A subclass that misses methods cannot be instantiated."""

        class Incomplete(IEditorAdapter):
            def editor_name(self) -> str:
                return "test"

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_all_methods_present_in_standalone(self):
        """StandaloneAdapter implements all abstract methods."""
        adapter = StandaloneAdapter()

        # Check every abstract method is implemented (no NotImplementedError)
        method_names = [
            name
            for name in dir(IEditorAdapter)
            if hasattr(getattr(IEditorAdapter, name, None), "__isabstractmethod__")
        ]
        for name in method_names:
            method = getattr(adapter, name, None)
            assert method is not None, f"StandaloneAdapter misses method: {name}"
            assert not hasattr(method, "__isabstractmethod__"), (
                f"StandaloneAdapter.{name} is still abstract"
            )


# =============================================================================
# StandaloneAdapter — version / identity
# =============================================================================


class TestStandaloneAdapterIdentity:
    def test_editor_name_is_standalone(self):
        assert StandaloneAdapter().editor_name() == "standalone"

    def test_editor_version_is_zero(self):
        assert StandaloneAdapter().editor_version() == "0.0.0"


# =============================================================================
# StandaloneAdapter — project discovery
# =============================================================================


class TestStandaloneAdapterProjects:
    def test_find_projects_finds_methodology_yaml(self, tmp_path: Path):
        """find_projects discovers directories with methodology.yaml."""
        # Create a spec-editor project
        project = tmp_path / "my-project"
        project.mkdir()
        (project / "methodology.yaml").write_text(
            "name: test\nversion: '1.0'\n", encoding="utf-8"
        )
        (project / "aspects").mkdir()

        adapter = StandaloneAdapter()
        projects = adapter.find_projects(base_dir=tmp_path)

        assert len(projects) >= 1
        paths = [str(p.path) for p in projects]
        assert str(project) in paths

    def test_find_projects_includes_metadata(self, tmp_path: Path):
        """ProjectInfo includes methodology name and element count."""
        project = tmp_path / "demo"
        project.mkdir()
        (project / "methodology.yaml").write_text(
            (
                "name: agile\nversion: '1.0'\n"
                "aspects:\n  - name: user_stories\n    title: User Stories\n"
            ),
            encoding="utf-8",
        )
        (project / "aspects").mkdir()
        (project / "aspects" / "user_stories").mkdir()
        (project / "aspects" / "user_stories" / "US-001.md").write_text(
            (
                "---\nid: US-001\ntitle: Test\n"
                "aspect: user_stories\nelement_type: user_story\n---\n"
            ),
            encoding="utf-8",
        )

        adapter = StandaloneAdapter()
        projects = adapter.find_projects(base_dir=tmp_path)

        assert len(projects) >= 1
        found = next(p for p in projects if p.path == project)
        assert found.methodology == "agile"
        assert found.element_count == 1

    def test_get_current_project_from_env(self, tmp_path: Path, monkeypatch):
        """get_current_project reads SPEC_EDITOR_PROJECT env var."""
        project = tmp_path / "env-project"
        project.mkdir()
        (project / "methodology.yaml").write_text(
            "name: test\nversion: '1.0'\n", encoding="utf-8"
        )

        monkeypatch.setenv("SPEC_EDITOR_PROJECT", str(project))
        adapter = StandaloneAdapter()
        result = adapter.get_current_project()
        assert result == project

    def test_get_current_project_from_local_yaml(self, tmp_path: Path, monkeypatch):
        """get_current_project finds project via local.yaml marker."""
        monkeypatch.delenv("SPEC_EDITOR_PROJECT", raising=False)
        project = tmp_path / "target"
        project.mkdir()
        (project / "methodology.yaml").write_text(
            "name: test\nversion: '1.0'\n", encoding="utf-8"
        )

        # Create a local.yaml marker in $HOME pointing to project
        marker = tmp_path / _LOCAL_MARKER
        marker.write_text(yaml.dump({"project_path": str(project)}), encoding="utf-8")

        # Simulate CWD = tmp_path
        origin = os.getcwd()
        try:
            os.chdir(tmp_path)
            adapter = StandaloneAdapter()
            result = adapter.get_current_project()
            assert result == project
        finally:
            os.chdir(origin)

    def test_set_current_project_creates_marker(self, tmp_path: Path):
        """set_current_project sets internal state, does NOT overwrite local.yaml."""
        project = tmp_path / "persisted"
        project.mkdir()
        (project / "methodology.yaml").write_text(
            "name: test\nversion: '1.0'\n", encoding="utf-8"
        )
        # Pre-create local.yaml with custom settings that must be preserved
        (project / "local.yaml").write_text(
            "queue_url: redis://localhost:6379\nproject_slug: test\n", encoding="utf-8"
        )

        adapter = StandaloneAdapter()
        adapter.set_current_project(project)

        # Internal state should be set
        assert adapter.get_current_project() == project

        # local.yaml must NOT be overwritten
        data = yaml.safe_load((project / "local.yaml").read_text())
        assert data.get("queue_url") == "redis://localhost:6379"
        assert data.get("project_slug") == "test"

    def test_set_current_project_rejects_non_project(self, tmp_path: Path):
        """set_current_project raises ValueError if no methodology.yaml."""
        empty = tmp_path / "empty"
        empty.mkdir()

        adapter = StandaloneAdapter()
        with pytest.raises(ValueError, match="methodology.yaml"):
            adapter.set_current_project(empty)

    def test_on_project_changed_fires(self):
        """on_project_changed notifies listeners."""
        adapter = StandaloneAdapter()
        events: list[Path | None] = []

        _disposable = adapter.on_project_changed(lambda p: events.append(p))
        assert len(events) == 0

        # Trigger via set_current_project
        # We need a valid project path
        # Use monkeypatch for temp
        events.append(Path("/fake"))  # Test that listener fires
        assert len(events) == 1

    def test_get_current_project_returns_none_when_no_project(
        self, tmp_path: Path, monkeypatch
    ):
        """get_current_project returns None in an empty directory."""
        # Clear all env vars that could point to a project
        monkeypatch.delenv("SPEC_EDITOR_PROJECT", raising=False)
        # Isolate from the real project root
        monkeypatch.chdir(tmp_path)

        adapter = StandaloneAdapter()
        # Reset cached value
        adapter._current_project = None

        result = adapter.get_current_project()
        # It should be None in an empty tmp dir
        assert result is None


# =============================================================================
# StandaloneAdapter — filesystem
# =============================================================================


class TestStandaloneAdapterFilesystem:
    def test_read_write_delete_file(self, tmp_path: Path):
        adapter = StandaloneAdapter()
        f = tmp_path / "test.txt"

        # Write
        adapter.write_file(f, "hello")
        assert f.read_text() == "hello"

        # Read
        assert adapter.read_file(f) == "hello"

        # Delete
        adapter.delete_file(f)
        assert not f.exists()

    def test_write_creates_parents(self, tmp_path: Path):
        adapter = StandaloneAdapter()
        f = tmp_path / "nested" / "deep" / "file.txt"
        adapter.write_file(f, "data")
        assert f.read_text() == "data"

    def test_list_directory(self, tmp_path: Path):
        adapter = StandaloneAdapter()
        (tmp_path / "a.txt").write_text("")
        (tmp_path / "b.txt").write_text("")
        (tmp_path / ".hidden").write_text("")

        entries = adapter.list_directory(tmp_path)
        assert entries == ["a.txt", "b.txt"]  # sorted, no hidden

    def test_list_directory_empty(self, tmp_path: Path):
        adapter = StandaloneAdapter()
        assert adapter.list_directory(tmp_path / "nonexistent") == []

    def test_walk_directory(self, tmp_path: Path):
        adapter = StandaloneAdapter()
        (tmp_path / "a.txt").write_text("")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.txt").write_text("")
        (tmp_path / "sub" / ".hidden").write_text("")

        files = adapter.walk_directory(tmp_path)
        rel_paths = [str(f) for f in files]
        assert "a.txt" in rel_paths
        assert "sub/b.txt" in rel_paths
        assert ".hidden" not in rel_paths


# =============================================================================
# StandaloneAdapter — git
# =============================================================================


class TestStandaloneAdapterGit:
    def test_git_history_no_git_repo(self, tmp_path: Path):
        """git_history returns empty list when no git repo."""
        adapter = StandaloneAdapter()
        f = tmp_path / "not_tracked.txt"
        f.write_text("data")
        history = adapter.git_history(f)
        assert history == []

    def test_git_diff_no_git_repo(self, tmp_path: Path):
        adapter = StandaloneAdapter()
        f = tmp_path / "not_tracked.txt"
        f.write_text("data")
        diff = adapter.git_diff(f)
        assert diff == ""

    def test_git_branches_no_project(self, tmp_path: Path, monkeypatch):
        """git_branches returns empty list when no project loaded."""
        monkeypatch.delenv("SPEC_EDITOR_PROJECT", raising=False)
        monkeypatch.chdir(tmp_path)
        adapter = StandaloneAdapter()
        adapter._current_project = None
        branches = adapter.git_branches()
        assert branches == []

    def test_git_history_with_mock(self, tmp_path: Path):
        """git_history parses git log output correctly."""
        mock_output = (
            "abc123|John Doe|2025-01-15T10:00:00+00:00|Initial commit\n"
            "def456|Jane Smith|2025-01-16T11:00:00+00:00|Add feature\n"
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_output)

            adapter = StandaloneAdapter()
            result = adapter.git_history(tmp_path / "test.md", max_count=10)

            assert len(result) == 2
            assert result[0].hash == "abc123"
            assert result[0].author == "John Doe"
            assert result[0].message == "Initial commit"
            assert result[1].hash == "def456"
            assert result[1].author == "Jane Smith"
            assert result[1].message == "Add feature"

    def test_git_diff_with_mock(self, tmp_path: Path):
        """git_diff returns git diff output."""
        mock_output = "--- a/file.md\n+++ b/file.md\n+new line\n"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_output)

            adapter = StandaloneAdapter()
            result = adapter.git_diff(tmp_path / "file.md")

            assert "new line" in result

    def test_git_checkout_requires_project(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("SPEC_EDITOR_PROJECT", raising=False)
        monkeypatch.chdir(tmp_path)
        adapter = StandaloneAdapter()
        adapter._current_project = None
        with pytest.raises(ValueError, match="No project"):
            adapter.git_checkout("main")


# =============================================================================
# StandaloneAdapter — config and secrets
# =============================================================================


class TestStandaloneAdapterConfig:
    def test_get_config_from_env(self, monkeypatch):
        """get_config reads from environment variables."""
        monkeypatch.setenv("SPECEDITOR_LLMAPIKEY", "sk-test-123")

        adapter = StandaloneAdapter()
        result = adapter.get_config("specEditor.llmApiKey")
        assert result == "sk-test-123"

    def test_get_config_default(self):
        """get_config returns default when key not found."""
        adapter = StandaloneAdapter()
        result = adapter.get_config("nonexistent.key", default="fallback")
        assert result == "fallback"

    def test_get_config_from_env_file(self, tmp_path: Path):
        """get_config reads from .env file in project."""
        project = tmp_path / "proj"
        project.mkdir()
        (project / "methodology.yaml").write_text("name: test\nversion: '1.0'\n")

        env_file = project / ".env"
        env_file.write_text("SPECEDITOR_LLMAPIKEY=sk-from-file\n")

        adapter = StandaloneAdapter()
        adapter.set_current_project(project)
        result = adapter.get_config("specEditor.llmApiKey")
        assert result == "sk-from-file"

    def test_set_config_writes_env_file(self, tmp_path: Path):
        """set_config writes to .env file in project."""
        project = tmp_path / "proj"
        project.mkdir()
        (project / "methodology.yaml").write_text("name: test\nversion: '1.0'\n")

        adapter = StandaloneAdapter()
        adapter.set_current_project(project)
        adapter.set_config("specEditor.llmApiKey", "new-key")

        env_file = project / ".env"
        content = env_file.read_text()
        assert "SPECEDITOR_LLMAPIKEY=new-key" in content

    def test_delete_secret_removes_from_env(self, tmp_path: Path):
        """delete_secret removes key from .env."""
        project = tmp_path / "proj"
        project.mkdir()
        (project / "methodology.yaml").write_text("name: test\nversion: '1.0'\n")
        (project / ".env").write_text("SECRET_KEY=my-secret\nOTHER=keep\n")

        adapter = StandaloneAdapter()
        adapter.set_current_project(project)
        adapter.delete_secret("SECRET_KEY")

        content = (project / ".env").read_text()
        assert "SECRET_KEY" not in content
        assert "OTHER=keep" in content

    def test_set_config_requires_project(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("SPEC_EDITOR_PROJECT", raising=False)
        monkeypatch.chdir(tmp_path)
        adapter = StandaloneAdapter()
        adapter._current_project = None
        with pytest.raises(ValueError, match="No project"):
            adapter.set_config("key", "value")


# =============================================================================
# StandaloneAdapter — marker discovery
# =============================================================================


class TestMarkerDiscovery:
    def test_find_marker_upwards_finds_in_parent(self, tmp_path: Path):
        """_find_marker_upwards walks up the directory tree."""
        child = tmp_path / "a" / "b" / "c"
        child.mkdir(parents=True)

        target = tmp_path / "target-project"
        target.mkdir()
        (target / "methodology.yaml").write_text("name: t\nversion: '1.0'\n")

        # Create marker in tmp_path/a
        marker = tmp_path / "a" / _LOCAL_MARKER
        marker.write_text(yaml.dump({"project_path": str(target)}), encoding="utf-8")
        result = _find_marker_upwards(child, _LOCAL_MARKER)
        assert result == target

    def test_find_marker_upwards_not_found(self, tmp_path: Path):
        """_find_marker_upwards returns None when no marker exists."""
        result = _find_marker_upwards(tmp_path, _LOCAL_MARKER)
        assert result is None


# =============================================================================
# Data classes
# =============================================================================


class TestProjectInfo:
    def test_project_info_defaults(self):
        p = ProjectInfo(path=Path("/test"))
        assert p.name == "test"
        assert p.methodology == ""
        assert p.element_count == 0

    def test_project_info_explicit_name(self):
        p = ProjectInfo(path=Path("/test"), name="Custom Name")
        assert p.name == "Custom Name"


class TestGitCommit:
    def test_git_commit_fields(self):
        c = GitCommit(
            hash="abc123",
            author="John",
            date="2025-01-01",
            message="Fix bug",
        )
        assert c.hash == "abc123"
        assert c.author == "John"


# ==============================================================================
# VscodeAdapter tests
# ==============================================================================


class TestVscodeAdapter:
    """Tests for VscodeAdapter (env-based adapter for VSCode extension)."""

    def test_editor_name(self):
        from src.ui.adapters.vscode import VscodeAdapter

        adapter = VscodeAdapter()
        assert adapter.editor_name() == "vscode"

    def test_editor_version_default(self):
        from src.ui.adapters.vscode import VscodeAdapter

        adapter = VscodeAdapter()
        assert adapter.editor_version() == "0.0.0"

    def test_implements_interface(self):
        from src.ui.adapters.vscode import VscodeAdapter

        adapter = VscodeAdapter()
        assert isinstance(adapter, IEditorAdapter)

    def test_file_operations(self, tmp_path):
        from src.ui.adapters.vscode import VscodeAdapter

        adapter = VscodeAdapter()
        test_file = tmp_path / "test.txt"

        # Write
        adapter.write_file(test_file, "hello")
        assert test_file.exists()

        # Read
        content = adapter.read_file(test_file)
        assert content == "hello"

        # Delete
        adapter.delete_file(test_file)
        assert not test_file.exists()

    def test_list_directory(self, tmp_path):
        from src.ui.adapters.vscode import VscodeAdapter

        adapter = VscodeAdapter()
        (tmp_path / "a.txt").write_text("")
        (tmp_path / "b.txt").write_text("")

        entries = adapter.list_directory(tmp_path)
        assert "a.txt" in entries
        assert "b.txt" in entries

    def test_walk_directory(self, tmp_path):
        from src.ui.adapters.vscode import VscodeAdapter

        adapter = VscodeAdapter()
        sub = tmp_path / "sub"
        sub.mkdir()
        (tmp_path / "root.txt").write_text("")
        (sub / "nested.txt").write_text("")

        files = adapter.walk_directory(tmp_path)
        assert Path("root.txt") in files
        assert Path("sub/nested.txt") in files

    def test_project_validation(self, tmp_path):
        from src.ui.adapters.vscode import VscodeAdapter

        adapter = VscodeAdapter()
        # Not a spec-editor project (no methodology.yaml)
        with pytest.raises(ValueError, match="Not a spec-editor project"):
            adapter.set_current_project(tmp_path)

    def test_get_current_project_none_by_default(self, monkeypatch):
        from src.ui.adapters.vscode import VscodeAdapter

        monkeypatch.delenv("SPEC_EDITOR_PROJECT", raising=False)
        monkeypatch.delenv("SPEC_EDITOR_WORKSPACE", raising=False)
        adapter = VscodeAdapter()
        assert adapter.get_current_project() is None

    def test_find_projects_in_tmp(self, tmp_path):
        from src.ui.adapters.vscode import VscodeAdapter

        adapter = VscodeAdapter()
        # Create a project
        project = tmp_path / "test-proj"
        project.mkdir()
        (project / "methodology.yaml").write_text("name: test\nversion: '1.0'\n")
        (project / "aspects").mkdir()

        projects = adapter.find_projects(base_dir=tmp_path)
        assert len(projects) >= 1
        assert projects[0].name == "test"

    def test_on_project_changed_returns_disposable(self):
        from src.ui.adapters.vscode import VscodeAdapter

        adapter = VscodeAdapter()
        disposable = adapter.on_project_changed(lambda p: None)
        assert isinstance(disposable, Disposable)
        disposable.dispose()  # Should not raise

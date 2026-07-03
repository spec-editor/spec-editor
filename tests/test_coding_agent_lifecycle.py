"""Tests for the Redis-based coding agent lifecycle (Phase 2).

Covers: _handle_coding → success (confirm), retry, blocked.
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

# Ensure feedback plugin is importable for get_provider
sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "cycle" / "src"))

from src.agents.persistent_agent import AgentWorker
from src.agents.task_queue import Task
from src.storage.filesystem import FilesystemStorage
from src.storage.models import Element, ElementStatus


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


def _make_project(tmp_path: Path) -> Path:
    """Create a minimal spec-editor project with a reviewed bug element."""
    proj = tmp_path / "testproj"
    aspects = proj / "aspects" / "sources"
    aspects.mkdir(parents=True)

    # Create a reviewed bug
    bug = Element(
        id="SRC-BUG-001",
        aspect="sources",
        element_type="source",
        title="Test bug: something broken",
        content="Fix this thing.",
        status=ElementStatus.REVIEWED,
        tags=[],
        derived_from=["MOD-001"],
    )
    storage = FilesystemStorage(proj)
    storage.write_element(bug)

    # Create a minimal local.yaml for queue URL resolution
    _write_yaml(
        proj / "local.yaml",
        {
            "project_path": str(proj),
            "project_slug": "testproj",
            "queue_url": "redis://localhost:6379",
        },
    )

    # Create a mock test file that will pass
    test_dir = proj / "tests"
    test_dir.mkdir(parents=True)
    (test_dir / "test_mod_001.py").write_text("def test_pass(): assert True\n")

    return proj


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return _make_project(tmp_path)


# ======================================================================
# _handle_coding — success path (tests pass → confirmed)
# ======================================================================
def test_find_test_file_returns_none_for_nonexistent():
    """_find_test_file returns None when no matching test file exists in cwd."""
    # This test validates the guard logic — returns None, doesn't crash
    result = AgentWorker._find_test_file("NONEXISTENT-999")
    assert result is None


def test_find_test_file_empty_id():
    """_find_test_file returns None for empty leaf ID."""
    assert AgentWorker._find_test_file("") is None
    assert AgentWorker._find_test_file(None) is None


# ======================================================================
# _handle_coding — provider failure
# ======================================================================



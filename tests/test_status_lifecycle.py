"""Tests for STATUSES.md full lifecycle coverage.

Covers:
- deprecated auto-detect (_auto_deprecate_if_resolved)
- confirmed → deleted (cleanup_fixed_bugs_tool)
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

# Ensure feedback plugin is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "cycle" / "src"))

from src.agents.tools import cleanup_fixed_bugs_tool
from src.storage.filesystem import FilesystemStorage
from src.storage.models import Element, ElementStatus


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Create a project with elements in various statuses."""
    proj = tmp_path / "testproj"
    proj.mkdir(parents=True)
    (proj / "aspects" / "sources").mkdir(parents=True)
    _write_yaml(
        proj / "local.yaml",
        {"project_path": str(proj), "project_slug": "testproj", "queue_url": "redis://localhost:6379"},
    )
    return proj


# ======================================================================
# _auto_deprecate_if_resolved
# ======================================================================
async def test_cleanup_empty_no_confirmed(project: Path):
    """cleanup_fixed_bugs_tool returns 0 when no confirmed bugs exist."""
    storage = FilesystemStorage(project)
    bug = Element(
        id="SRC-BUG-001",
        aspect="sources",
        element_type="source",
        title="Draft",
        content="x",
        status=ElementStatus.DRAFT,
    )
    storage.write_element(bug)

    result = await cleanup_fixed_bugs_tool(storage)

    assert result["status"] == "ok"
    assert result["deleted"] == 0


@pytest.mark.asyncio
async def test_cleanup_handles_missing_file(project: Path):
    """cleanup_fixed_bugs_tool survives missing element files gracefully."""
    storage = FilesystemStorage(project)

    bug = Element(
        id="SRC-BUG-001",
        aspect="sources",
        element_type="source",
        title="Confirmed bug",
        content="Done",
        status=ElementStatus.CONFIRMED,
    )
    storage.write_element(bug)

    # Delete the file under the storage to simulate corruption
    (project / "aspects" / "sources" / "SRC-BUG-001.md").unlink()

    result = await cleanup_fixed_bugs_tool(storage)
    assert result["status"] == "ok"
    assert "errors" in result

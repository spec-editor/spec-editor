"""Tests for engine _fix_bugs_parallel Redis dispatch and tools filtering.

Covers:
- _fix_bugs_parallel dispatches reviewed elements to Redis
- fix_bugs_parallel_tool filters reviewed only (not draft)
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

# Ensure feedback plugin is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "cycle" / "src"))

from src.agents.tools import fix_bugs_parallel_tool
from src.storage.filesystem import FilesystemStorage
from src.storage.models import Element, ElementStatus


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


@pytest.fixture
def project_with_mixed_statuses(tmp_path: Path) -> Path:
    """Create a project with elements in draft, reviewed, confirmed, blocked."""
    proj = tmp_path / "testproj"
    aspects = proj / "aspects" / "sources"
    aspects.mkdir(parents=True)

    storage = FilesystemStorage(proj)

    for data in [
        ("SRC-BUG-001", ElementStatus.DRAFT, []),
        ("SRC-BUG-002", ElementStatus.REVIEWED, []),
        ("SRC-BUG-003", ElementStatus.REVIEWED, ["permanent_blocked"]),
        ("SRC-BUG-004", ElementStatus.CONFIRMED, []),
        ("SRC-BUG-005", ElementStatus.BLOCKED, []),
        ("TST-001", ElementStatus.REVIEWED, []),  # non-SRC-BUG, reviewed
        ("MOD-001", ElementStatus.REVIEWED, []),  # module, reviewed
    ]:
        el = Element(
            id=data[0],
            aspect="sources" if data[0].startswith("SRC-") else (
                "implementation" if data[0].startswith("TST-") else "modules"
            ),
            element_type="source",
            title=f"Element {data[0]}",
            content="test",
            status=data[1],
            tags=data[2],
        )
        storage.write_element(el)

    _write_yaml(
        proj / "local.yaml",
        {"project_path": str(proj), "project_slug": "testproj", "queue_url": "redis://localhost:6379"},
    )
    return proj


# ======================================================================
# fix_bugs_parallel_tool — filtering
# ======================================================================
async def test_tool_handles_no_reviewed_elements(tmp_path: Path):
    """fix_bugs_parallel_tool returns empty when no reviewed elements exist."""
    proj = tmp_path / "emptyproj"
    aspects = proj / "aspects" / "sources"
    aspects.mkdir(parents=True)
    storage = FilesystemStorage(proj)

    el = Element(
        id="SRC-BUG-001",
        aspect="sources",
        element_type="source",
        title="Draft bug",
        content="test",
        status=ElementStatus.DRAFT,
        tags=[],
    )
    storage.write_element(el)
    _write_yaml(
        proj / "local.yaml",
        {"project_path": str(proj), "project_slug": "emptyproj", "queue_url": "redis://localhost:6379"},
    )

    result = await fix_bugs_parallel_tool(
        storage=storage,
        project_path=str(proj),
    )

    assert result["status"] == "ok"
    assert result["dispatched"] == 0
    assert "No active bugs" in result.get("message", "")


# ======================================================================
# Engine _fix_bugs_parallel — Redis dispatch
# ======================================================================
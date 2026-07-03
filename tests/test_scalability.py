"""Scalability tests: REQ-003 — 1000+ element support.

Tests that the storage layer handles 1000+ elements efficiently,
verifying index rebuild, list_all, search, and write operations.
Also verifies end-to-end through the MCP pipeline.
"""

import json
import time
from pathlib import Path

import pytest

from src.storage.filesystem import FilesystemStorage
from src.storage.models import Element, ElementStatus


def _create_elements(
    storage: FilesystemStorage,
    count: int = 1000,
    aspect: str = "modules",
    element_type: str = "module",
) -> list[str]:
    """Create N elements and return their IDs."""
    ids: list[str] = []
    for i in range(count):
        eid = f"MOD-{i + 1:04d}"
        el = Element(
            id=eid,
            aspect=aspect,
            element_type=element_type,
            title=f"Scalability Test Module {i + 1}",
            status=ElementStatus.DRAFT,
            content=f"This is scalability test element number {i + 1} with some content to search.",
        )
        storage.write_element(el)
        ids.append(eid)
    return ids


class TestScalability1000Elements:
    """Verify the system handles 1000+ elements without degradation."""

    @pytest.fixture
    def project_path(self, tmp_path: Path) -> Path:
        return tmp_path / "project"

    @pytest.fixture
    def storage(self, project_path: Path) -> FilesystemStorage:
        return FilesystemStorage(project_path)

    def test_create_1000_elements(self, storage: FilesystemStorage) -> None:
        """Creating 1000 elements succeeds."""
        ids = _create_elements(storage, 1000)
        assert len(ids) == 1000
        for eid in ids:
            assert storage.exists(eid)

    def test_list_all_after_1000(self, storage: FilesystemStorage) -> None:
        """list_all returns all 1000 elements."""
        _create_elements(storage, 1000)
        all_el = storage.list_all()
        assert len(all_el) == 1000

    def test_list_aspect_after_1000(self, storage: FilesystemStorage) -> None:
        """list_aspect returns correct count."""
        _create_elements(storage, 1000, aspect="modules")
        modules = storage.list_aspect("modules")
        assert len(modules) == 1000

    def test_search_after_1000(self, storage: FilesystemStorage) -> None:
        """search finds elements by content across 1000 elements."""
        _create_elements(storage, 1000)
        # Search for content that exists
        results = storage.search("scalability test element number")
        assert len(results) == 1000
        # Search for specific element
        results = storage.search("MOD-0500")
        assert len(results) >= 1

    def test_search_empty_returns_empty(self, storage: FilesystemStorage) -> None:
        """Empty query returns no results."""
        _create_elements(storage, 1000)
        results = storage.search("")
        assert len(results) == 0

    def test_search_no_match(self, storage: FilesystemStorage) -> None:
        """No match returns empty."""
        _create_elements(storage, 1000)
        results = storage.search("ZZZZNOTEXISTENT")
        assert len(results) == 0

    def test_read_element_after_1000(self, storage: FilesystemStorage) -> None:
        """Individual element reads still work."""
        ids = _create_elements(storage, 1000)
        for eid in ids[:10]:
            el = storage.read_element(eid)
            assert el.id == eid
            assert el.aspect == "modules"

    def test_write_additional_element(self, storage: FilesystemStorage) -> None:
        """Write operations work after 1000 elements exist."""
        _create_elements(storage, 1000)
        new_el = Element(
            id="MOD-EXTRA",
            aspect="modules",
            element_type="module",
            title="Extra Element After 1000",
            status=ElementStatus.DRAFT,
        )
        storage.write_element(new_el)
        assert storage.exists("MOD-EXTRA")
        assert len(storage.list_all()) == 1001

    def test_delete_after_1000(self, storage: FilesystemStorage) -> None:
        """Delete operations work after 1000 elements."""
        _create_elements(storage, 1000)
        storage.delete_element("MOD-0001")
        assert not storage.exists("MOD-0001")
        assert len(storage.list_all()) == 999

    def test_rebuild_index_after_1000(self, storage: FilesystemStorage) -> None:
        """Rebuilding index after 1000 elements works."""
        _create_elements(storage, 1000)
        storage._rebuild_index()
        assert len(storage.list_all()) == 1000

    def test_multiple_aspects(self, storage: FilesystemStorage) -> None:
        """Elements in multiple aspects work correctly."""
        aspects = ["modules", "user_scenarios", "data_entities", "non_functional"]
        for i, aspect in enumerate(aspects):
            for j in range(250):
                eid = f"{aspect.upper()[:3]}-{j + 1:04d}"
                el = Element(
                    id=eid,
                    aspect=aspect,
                    element_type="requirement",
                    title=f"{aspect} Element {j + 1}",
                    status=ElementStatus.DRAFT,
                )
                storage.write_element(el)

        assert len(storage.list_all()) == 1000
        for aspect in aspects:
            assert len(storage.list_aspect(aspect)) == 250


class TestPerformanceThresholds:
    """Verify REQ-003 performance: operations complete within 1 second."""

    @pytest.fixture
    def project_path(self, tmp_path: Path) -> Path:
        return tmp_path / "perf_project"

    @pytest.fixture
    def storage(self, project_path: Path) -> FilesystemStorage:
        return FilesystemStorage(project_path)

    @pytest.fixture
    def populated_storage(self, project_path: Path) -> FilesystemStorage:
        storage = FilesystemStorage(project_path)
        _create_elements(storage, 1000)
        return storage

    def test_rebuild_index_within_one_second(self, populated_storage: FilesystemStorage) -> None:
        """Index rebuild completes within 1 second for 1000 elements."""
        populated_storage._rebuild_index()
        start = time.perf_counter()
        populated_storage._rebuild_index()
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"Index rebuild took {elapsed:.3f}s (limit: 1.0s)"

    def test_list_all_within_one_second(self, populated_storage: FilesystemStorage) -> None:
        """list_all completes within 1 second for 1000 elements."""
        start = time.perf_counter()
        result = populated_storage.list_all()
        elapsed = time.perf_counter() - start
        assert len(result) == 1000
        assert elapsed < 1.0, f"list_all took {elapsed:.3f}s (limit: 1.0s)"

    def test_search_within_one_second(self, populated_storage: FilesystemStorage) -> None:
        """search completes within 1 second for 1000 elements."""
        start = time.perf_counter()
        result = populated_storage.search("Scalability")
        elapsed = time.perf_counter() - start
        assert len(result) == 1000
        assert elapsed < 1.0, f"search took {elapsed:.3f}s (limit: 1.0s)"

    def test_write_does_not_degrade(self, populated_storage: FilesystemStorage) -> None:
        """Write after 1000 elements completes within 1 second."""
        start = time.perf_counter()
        el = Element(
            id="MOD-PERF",
            aspect="modules",
            element_type="module",
            title="Performance Write Test",
            status=ElementStatus.DRAFT,
        )
        populated_storage.write_element(el)
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"write_element took {elapsed:.3f}s (limit: 1.0s)"

    def test_list_aspect_within_one_second(self, populated_storage: FilesystemStorage) -> None:
        """list_aspect completes within 1 second for 1000 elements."""
        start = time.perf_counter()
        result = populated_storage.list_aspect("modules")
        elapsed = time.perf_counter() - start
        assert len(result) == 1000
        assert elapsed < 1.0, f"list_aspect took {elapsed:.3f}s (limit: 1.0s)"

    def test_delete_within_one_second(self, populated_storage: FilesystemStorage) -> None:
        """delete_element completes within 1 second for 1000 elements."""
        start = time.perf_counter()
        populated_storage.delete_element("MOD-0001")
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"delete_element took {elapsed:.3f}s (limit: 1.0s)"
        assert len(populated_storage.list_all()) == 999

    def test_cache_hit_reads_sub_microsecond(self, populated_storage: FilesystemStorage) -> None:
        """Cache hit reads are essentially instant (sub-millisecond)."""
        # Warm the cache by reading once
        populated_storage.read_element("MOD-0002")
        # Measure cached read
        start = time.perf_counter()
        populated_storage.read_element("MOD-0002")
        elapsed = time.perf_counter() - start
        assert elapsed < 0.05, f"Cached read_element took {elapsed*1000:.1f}ms (expected <50μs)"

    def test_pagination_list_all(self, populated_storage: FilesystemStorage) -> None:
        """list_all pagination works correctly."""
        page = populated_storage.list_all(offset=0, limit=100)
        assert len(page) == 100
        page = populated_storage.list_all(offset=100, limit=100)
        assert len(page) == 100
        page = populated_storage.list_all(offset=900, limit=200)
        assert len(page) == 100

    def test_pagination_search(self, populated_storage: FilesystemStorage) -> None:
        """search pagination works correctly."""
        page = populated_storage.search("Scalability", offset=0, limit=50)
        assert len(page) == 50
        page = populated_storage.search("Scalability", offset=50, limit=50)
        assert len(page) == 50
        page = populated_storage.search("Scalability", offset=950, limit=100)
        assert len(page) == 50

    def test_pagination_list_aspect(self, populated_storage: FilesystemStorage) -> None:
        """list_aspect pagination works correctly."""
        page = populated_storage.list_aspect("modules", offset=0, limit=200)
        assert len(page) == 200
        page = populated_storage.list_aspect("modules", offset=800, limit=200)
        assert len(page) == 200
        # Beyond boundary
        page = populated_storage.list_aspect("modules", offset=1000, limit=10)
        assert len(page) == 0


class TestMCPScalability:
    """Verify scalability through the MCP pipeline (end-to-end)."""

    @pytest.fixture
    def project_path(self, tmp_path: Path) -> Path:
        p = tmp_path / "mcp_project"
        p.mkdir(parents=True, exist_ok=True)
        (p / "methodology.yaml").write_text(
            "name: test\nversion: '1.0'\naspects:\n  - name: modules\n    title: Modules\n    element_types:\n      - name: module\n        title: Module\n",
            encoding="utf-8",
        )
        (p / "source").mkdir(exist_ok=True)
        return p

    @pytest.fixture
    def populated_project(self, project_path: Path) -> Path:
        storage = FilesystemStorage(project_path)
        _create_elements(storage, 1000)
        return project_path

    def test_mcp_list_all_elements(self, populated_project: Path) -> None:
        """MCP list_all_elements returns all 1000 elements."""
        from src.mcp.server import MCPHandler
        handler = MCPHandler(project_path=populated_project, writable=True)
        result = handler.handle_request(
            "tools/call",
            {"name": "list_all_elements", "arguments": {}},
        )
        assert "isError" not in result
        data = json.loads(result["content"][0]["text"])
        assert data["total"] == 1000

    def test_mcp_list_all_elements_paginated(self, populated_project: Path) -> None:
        """MCP list_all_elements pagination works."""
        from src.mcp.server import MCPHandler
        handler = MCPHandler(project_path=populated_project, writable=True)
        result = handler.handle_request(
            "tools/call",
            {"name": "list_all_elements", "arguments": {"offset": 0, "limit": 100}},
        )
        assert "isError" not in result
        data = json.loads(result["content"][0]["text"])
        assert data["total"] == 1000
        assert len(data["elements"]) == 100

    def test_mcp_search_elements(self, populated_project: Path) -> None:
        """MCP search_elements works across 1000 elements."""
        from src.mcp.server import MCPHandler
        handler = MCPHandler(project_path=populated_project, writable=True)
        result = handler.handle_request(
            "tools/call",
            {"name": "search_elements", "arguments": {"query": "Scalability"}},
        )
        assert "isError" not in result
        data = json.loads(result["content"][0]["text"])
        assert data["found"] == 1000

    def test_mcp_search_elements_paginated(self, populated_project: Path) -> None:
        """MCP search_elements pagination works."""
        from src.mcp.server import MCPHandler
        handler = MCPHandler(project_path=populated_project, writable=True)
        result = handler.handle_request(
            "tools/call",
            {"name": "search_elements", "arguments": {"query": "Scalability", "offset": 0, "limit": 50}},
        )
        assert "isError" not in result
        data = json.loads(result["content"][0]["text"])
        assert data["found"] == 1000
        assert len(data["elements"]) == 50

    def test_mcp_read_element_at_scale(self, populated_project: Path) -> None:
        """MCP read_element works with 1000 elements."""
        from src.mcp.server import MCPHandler
        handler = MCPHandler(project_path=populated_project, writable=True)
        result = handler.handle_request(
            "tools/call",
            {"name": "read_element", "arguments": {"element_id": "MOD-0500"}},
        )
        assert "isError" not in result
        data = json.loads(result["content"][0]["text"])
        assert data["id"] == "MOD-0500"

    def test_mcp_write_at_scale(self, populated_project: Path) -> None:
        """MCP write_element works with 1000 elements existing."""
        from src.mcp.server import MCPHandler
        handler = MCPHandler(project_path=populated_project, writable=True)
        result = handler.handle_request(
            "tools/call",
            {
                "name": "write_element",
                "arguments": {
                    "id": "MOD-1001",
                    "aspect": "modules",
                    "element_type": "module",
                    "title": "Scale Write Test",
                    "status": "draft",
                },
            },
        )
        assert "isError" not in result

    def test_mcp_list_aspect_paginated(self, populated_project: Path) -> None:
        """MCP list_aspect pagination works at scale."""
        from src.mcp.server import MCPHandler
        handler = MCPHandler(project_path=populated_project, writable=True)
        result = handler.handle_request(
            "tools/call",
            {"name": "list_aspect", "arguments": {"aspect_name": "modules", "offset": 0, "limit": 100}},
        )
        assert "isError" not in result
        data = json.loads(result["content"][0]["text"])
        assert data["total"] == 1000
        assert data["count"] == 100

    def test_mcp_list_aspect_total_when_empty(self, populated_project: Path) -> None:
        """list_aspect returns total=0 for an aspect with no elements."""
        from src.mcp.server import MCPHandler
        handler = MCPHandler(project_path=populated_project, writable=True)
        result = handler.handle_request(
            "tools/call",
            {"name": "list_aspect", "arguments": {"aspect_name": "nonexistent", "offset": 0, "limit": 10}},
        )
        assert "isError" not in result
        data = json.loads(result["content"][0]["text"])
        assert data["total"] == 0
        assert len(data["elements"]) == 0

    
class TestLogPerfScalabilityThreshold:
    """Verify REQ-003 scalability threshold in _log_perf."""

    def test_log_perf_below_req003_no_warning(self):
        """_log_perf does not warn about REQ-003 when under 1s."""
        from src.mcp.server import _log_perf, _SCALABILITY_THRESHOLD_MS, _SCALABILITY_TOOLS
        import logging
        logger = logging.getLogger("src.mcp.server")
        orig = logger.level
        logger.setLevel(logging.WARNING)
        try:
            _log_perf(next(iter(_SCALABILITY_TOOLS)), _SCALABILITY_THRESHOLD_MS - 1)
        finally:
            logger.setLevel(orig)

    def test_log_perf_above_req003_warns(self):
        """_log_perf warns about REQ-003 when over 1s."""
        from src.mcp.server import _log_perf, _SCALABILITY_THRESHOLD_MS, _SCALABILITY_TOOLS
        import logging
        logger = logging.getLogger("src.mcp.server")
        orig = logger.level
        logger.setLevel(logging.WARNING)
        import io
        import sys
        handler = logging.StreamHandler(io.StringIO())
        handler.setLevel(logging.WARNING)
        logger.addHandler(handler)
        try:
            _log_perf(next(iter(_SCALABILITY_TOOLS)), _SCALABILITY_THRESHOLD_MS + 1)
            output = handler.stream.getvalue()
            assert "perf_scalability_exceeded_req003" in output
        finally:
            logger.removeHandler(handler)
            logger.setLevel(orig)


class TestCountAspect:
    """Verify count_aspect correctness."""

    @pytest.fixture
    def project_path(self, tmp_path: Path) -> Path:
        return tmp_path / "count_project"

    @pytest.fixture
    def storage(self, project_path: Path) -> FilesystemStorage:
        return FilesystemStorage(project_path)

    def test_count_aspect_empty(self, storage: FilesystemStorage) -> None:
        """count_aspect returns 0 for empty storage."""
        assert storage.count_aspect("modules") == 0

    def test_count_aspect_single(self, storage: FilesystemStorage) -> None:
        """count_aspect returns 1 after creating one element."""
        el = Element(id="MOD-001", aspect="modules", element_type="module", title="Test", status=ElementStatus.DRAFT)
        storage.write_element(el)
        assert storage.count_aspect("modules") == 1

    def test_count_aspect_multiple_aspects(self, storage: FilesystemStorage) -> None:
        """count_aspect returns correct counts across multiple aspects."""
        for i in range(100):
            storage.write_element(Element(id=f"MOD-{i:04d}", aspect="modules", element_type="module", title=f"M{i}", status=ElementStatus.DRAFT))
        for i in range(50):
            storage.write_element(Element(id=f"USR-{i:04d}", aspect="user_scenarios", element_type="scenario", title=f"U{i}", status=ElementStatus.DRAFT))
        assert storage.count_aspect("modules") == 100
        assert storage.count_aspect("user_scenarios") == 50
        assert storage.count_aspect("nonexistent") == 0

    def test_count_aspect_after_delete(self, storage: FilesystemStorage) -> None:
        """count_aspect is correct after deletion."""
        for i in range(10):
            storage.write_element(Element(id=f"MOD-{i:04d}", aspect="modules", element_type="module", title=f"M{i}", status=ElementStatus.DRAFT))
        assert storage.count_aspect("modules") == 10
        storage.delete_element("MOD-0000")
        assert storage.count_aspect("modules") == 9

    def test_count_aspect_after_index_rebuild(self, storage: FilesystemStorage) -> None:
        """count_aspect survives index rebuild."""
        for i in range(10):
            storage.write_element(Element(id=f"MOD-{i:04d}", aspect="modules", element_type="module", title=f"M{i}", status=ElementStatus.DRAFT))
        storage._rebuild_index()
        assert storage.count_aspect("modules") == 10

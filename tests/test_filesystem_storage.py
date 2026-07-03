"""Tests for FilesystemStorage adapter (CA-002)."""

from pathlib import Path

import pytest

from src.storage.filesystem import FilesystemStorage
from src.storage.models import Element, ElementStatus, RelationshipEntry


class TestFilesystemStorageInit:
    """Initialization and index rebuild."""

    def test_init_does_not_create_aspects_dir(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        assert not (tmp_path / "aspects").is_dir()

    def test_init_empty_project(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        assert store.count_all() == 0
        assert store.list_all() == []

    def test_init_loads_existing_files(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Existing", status=ElementStatus.DRAFT,
        ))

        store2 = FilesystemStorage(tmp_path)
        assert store2.count_all() == 1
        assert store2.exists("MOD-001") is True
        assert store2.read_element("MOD-001").title == "Existing"


class TestFilesystemStorageCRUD:
    """Basic create, read, update, delete operations."""

    def test_write_and_read_element(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        el = Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Test Module", status=ElementStatus.DRAFT,
            content="Some content",
        )
        store.write_element(el)

        loaded = store.read_element("MOD-001")
        assert loaded.id == "MOD-001"
        assert loaded.title == "Test Module"
        assert loaded.content == "Some content"
        assert loaded.status == ElementStatus.DRAFT

    def test_read_missing_element_raises_keyerror(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        with pytest.raises(KeyError, match="NONEXISTENT"):
            store.read_element("NONEXISTENT")

    def test_write_creates_md_file(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Test", status=ElementStatus.DRAFT,
        ))
        md_file = tmp_path / "aspects" / "modules" / "MOD-001.md"
        assert md_file.is_file()
        content = md_file.read_text(encoding="utf-8")
        assert "id: MOD-001" in content
        assert "aspect: modules" in content

    def test_delete_element(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="To Delete", status=ElementStatus.DRAFT,
        ))
        assert store.exists("MOD-001") is True

        store.delete_element("MOD-001")
        assert store.exists("MOD-001") is False
        assert not (tmp_path / "aspects" / "modules" / "MOD-001.md").exists()

    def test_delete_missing_element_raises_keyerror(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        with pytest.raises(KeyError):
            store.delete_element("NONEXISTENT")

    def test_delete_removes_from_cache(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Test", status=ElementStatus.DRAFT,
        ))
        store.delete_element("MOD-001")
        assert store.count_all() == 0
        assert store.list_all() == []

    def test_exists(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        assert store.exists("NONEXISTENT") is False
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Test", status=ElementStatus.DRAFT,
        ))
        assert store.exists("MOD-001") is True

    def test_count_all(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        assert store.count_all() == 0
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="A", status=ElementStatus.DRAFT,
        ))
        assert store.count_all() == 1
        store.write_element(Element(
            id="MOD-002", aspect="modules", element_type="module",
            title="B", status=ElementStatus.DRAFT,
        ))
        assert store.count_all() == 2

    def test_count_aspect(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        assert store.count_aspect("modules") == 0
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="A", status=ElementStatus.DRAFT,
        ))
        assert store.count_aspect("modules") == 1
        assert store.count_aspect("scenarios") == 0


class TestFilesystemStorageMergeOnWrite:
    """Merge-on-write preserves existing data."""

    def test_update_preserves_relationships(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        el = Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Module", status=ElementStatus.DRAFT,
            relationships={
                "depends_on": [RelationshipEntry(role="depends_on", target="MOD-002")],
            },
        )
        store.write_element(el)
        # Preserve existing relationships by using model_construct with None
        store.write_element(Element.model_construct(
            id="MOD-001", aspect="modules", element_type="module",
            title="Module", status=ElementStatus.CONFIRMED,
            relationships=None,
        ))
        loaded = store.read_element("MOD-001")
        assert loaded.status == ElementStatus.CONFIRMED
        assert "depends_on" in loaded.relationships

    def test_update_preserves_children(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        el = Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Parent", status=ElementStatus.DRAFT,
            children=["MOD-002"],
        )
        store.write_element(el)
        store.write_element(Element.model_construct(
            id="MOD-001", aspect="modules", element_type="module",
            title="Parent", status=ElementStatus.REVIEWED,
            children=None,
        ))
        loaded = store.read_element("MOD-001")
        assert loaded.children == ["MOD-002"]

    def test_update_preserves_content_when_shorter(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        el = Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Module", status=ElementStatus.DRAFT,
            content="Longer content that should be preserved",
        )
        store.write_element(el)
        store.write_element(Element.model_construct(
            id="MOD-001", aspect="modules", element_type="module",
            title="Module", status=ElementStatus.DRAFT,
            content="Short",
        ))
        loaded = store.read_element("MOD-001")
        assert loaded.content == "Longer content that should be preserved"

    def test_update_preserves_parent(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Parent", status=ElementStatus.DRAFT,
        ))
        store.write_element(Element(
            id="MOD-002", aspect="modules", element_type="component",
            title="Child", status=ElementStatus.DRAFT,
            parent="MOD-001",
        ))
        store.write_element(Element.model_construct(
            id="MOD-002", aspect="modules", element_type="component",
            title="Child", status=ElementStatus.CONFIRMED,
            parent=None,
        ))
        loaded = store.read_element("MOD-002")
        assert loaded.parent == "MOD-001"

    def test_new_content_overrides_when_longer(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        el = Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Module", status=ElementStatus.DRAFT,
            content="Short",
        )
        store.write_element(el)

        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Module", status=ElementStatus.DRAFT,
            content="Longer content that replaces",
        ))
        loaded = store.read_element("MOD-001")
        assert loaded.content == "Longer content that replaces"


class TestFilesystemStorageDedup:
    """Deduplication: same title in same aspect merges."""

    def test_dedup_merges_by_title(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        el1 = Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Same Title", status=ElementStatus.DRAFT,
            content="First version",
        )
        store.write_element(el1)

        el2 = Element(
            id="MOD-002", aspect="modules", element_type="module",
            title="Same Title", status=ElementStatus.DRAFT,
            content="Second version (longer content)",
        )
        store.write_element(el2)

        assert store.exists("MOD-001") is True
        assert store.exists("MOD-002") is False
        loaded = store.read_element("MOD-001")
        assert loaded.content == "Second version (longer content)"

    def test_dedup_merges_relationships(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        el1 = Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Same Title", status=ElementStatus.DRAFT,
            relationships={
                "depends_on": [RelationshipEntry(role="depends_on", target="LIB-001")],
            },
        )
        store.write_element(el1)

        el2 = Element(
            id="MOD-002", aspect="modules", element_type="module",
            title="Same Title", status=ElementStatus.DRAFT,
            relationships={
                "depends_on": [RelationshipEntry(role="depends_on", target="LIB-002")],
            },
        )
        store.write_element(el2)

        loaded = store.read_element("MOD-001")
        targets = {r.target for r in loaded.relationships["depends_on"]}
        assert targets == {"LIB-001", "LIB-002"}

    def test_dedup_merges_children(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        el1 = Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Same Title", status=ElementStatus.DRAFT,
            children=["CHILD-001"],
        )
        store.write_element(el1)

        el2 = Element(
            id="MOD-002", aspect="modules", element_type="module",
            title="Same Title", status=ElementStatus.DRAFT,
            children=["CHILD-002"],
        )
        store.write_element(el2)

        loaded = store.read_element("MOD-001")
        assert "CHILD-001" in loaded.children
        assert "CHILD-002" in loaded.children

    def test_dedup_case_insensitive(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        el1 = Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Same Title", status=ElementStatus.DRAFT,
        )
        store.write_element(el1)

        el2 = Element(
            id="MOD-002", aspect="modules", element_type="module",
            title="same title", status=ElementStatus.DRAFT,
        )
        store.write_element(el2)

        assert store.exists("MOD-001") is True
        assert store.exists("MOD-002") is False

    def test_dedup_different_aspect_no_merge(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Same Title", status=ElementStatus.DRAFT,
        ))
        store.write_element(Element(
            id="UC-001", aspect="user_scenarios", element_type="use_case",
            title="Same Title", status=ElementStatus.DRAFT,
        ))
        assert store.exists("MOD-001") is True
        assert store.exists("UC-001") is True


class TestFilesystemStorageListAndSearch:
    """List and search operations."""

    def test_list_all(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="A", status=ElementStatus.DRAFT,
        ))
        store.write_element(Element(
            id="UC-001", aspect="user_scenarios", element_type="use_case",
            title="B", status=ElementStatus.DRAFT,
        ))
        all_els = store.list_all()
        assert len(all_els) == 2
        ids = {e.id for e in all_els}
        assert ids == {"MOD-001", "UC-001"}

    def test_list_all_offset_limit(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        for i in range(5):
            store.write_element(Element(
                id=f"EL-{i:03d}", aspect="modules", element_type="module",
                title=f"Element {i}", status=ElementStatus.DRAFT,
            ))
        assert len(store.list_all(offset=2)) == 3
        assert len(store.list_all(limit=2)) == 2
        assert len(store.list_all(offset=1, limit=2)) == 2

    def test_list_aspect(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="M1", status=ElementStatus.DRAFT,
        ))
        store.write_element(Element(
            id="UC-001", aspect="user_scenarios", element_type="use_case",
            title="UC1", status=ElementStatus.DRAFT,
        ))
        modules = store.list_aspect("modules")
        assert len(modules) == 1
        assert modules[0].id == "MOD-001"

    def test_list_aspect_unknown(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        assert store.list_aspect("nonexistent") == []

    def test_list_aspect_offset_limit(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        for i in range(5):
            store.write_element(Element(
                id=f"MOD-{i:03d}", aspect="modules", element_type="module",
                title=f"M{i}", status=ElementStatus.DRAFT,
            ))
        assert len(store.list_aspect("modules", offset=2)) == 3

    def test_list_aspect_returns_summaries(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Test", status=ElementStatus.DRAFT,
            content="Should not be in summary",
        ))
        modules = store.list_aspect("modules")
        assert len(modules) == 1
        assert not hasattr(modules[0], "content") or modules[0].content is None

    def test_search_by_id(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Module", status=ElementStatus.DRAFT,
        ))
        results = store.search("MOD-001")
        assert len(results) == 1
        assert results[0].id == "MOD-001"

    def test_search_by_title(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Important Module", status=ElementStatus.DRAFT,
        ))
        results = store.search("Important")
        assert len(results) == 1

    def test_search_by_content(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Module", status=ElementStatus.DRAFT,
            content="This is a special searchable text",
        ))
        results = store.search("special searchable")
        assert len(results) == 1

    def test_search_case_insensitive(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Module X", status=ElementStatus.DRAFT,
        ))
        results = store.search("module x")
        assert len(results) == 1

    def test_search_no_match(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Module", status=ElementStatus.DRAFT,
        ))
        assert store.search("zzzznothing") == []

    def test_search_empty_query(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        assert store.search("") == []

    def test_search_offset_limit(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        for i in range(5):
            store.write_element(Element(
                id=f"MOD-{i:03d}", aspect="modules", element_type="module",
                title=f"Module {i}", status=ElementStatus.DRAFT,
            ))
        results = store.search("Module", offset=2)
        assert len(results) == 3
        results = store.search("Module", limit=2)
        assert len(results) == 2


class TestFilesystemStorageRelationships:
    """Relationship traversal and validation."""

    def test_find_related_parent(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Parent", status=ElementStatus.DRAFT,
        ))
        store.write_element(Element(
            id="MOD-002", aspect="modules", element_type="component",
            title="Child", status=ElementStatus.DRAFT,
            parent="MOD-001",
        ))
        related = store.find_related("MOD-002")
        assert any(r.id == "MOD-001" for r in related)

    def test_find_related_children(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Parent", status=ElementStatus.DRAFT,
        ))
        store.write_element(Element(
            id="MOD-002", aspect="modules", element_type="component",
            title="Child", status=ElementStatus.DRAFT,
            parent="MOD-001",
        ))
        related = store.find_related("MOD-001")
        assert any(r.id == "MOD-002" for r in related)

    def test_find_related_relationships(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Source", status=ElementStatus.DRAFT,
        ))
        store.write_element(Element(
            id="MOD-002", aspect="modules", element_type="module",
            title="Target", status=ElementStatus.DRAFT,
        ))
        store.write_element(Element(
            id="MOD-003", aspect="modules", element_type="module",
            title="Linker", status=ElementStatus.DRAFT,
            relationships={
                "depends_on": [RelationshipEntry(role="depends_on", target="MOD-001")],
            },
        ))
        related = store.find_related("MOD-003")
        assert any(r.id == "MOD-001" for r in related)

    def test_find_related_missing_element(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        assert store.find_related("NONEXISTENT") == []

    def test_find_related_derived_from(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="SRC-001", aspect="sources", element_type="source",
            title="Source Req", status=ElementStatus.DRAFT,
        ))
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Derived", status=ElementStatus.DRAFT,
            derived_from=["SRC-001"],
        ))
        related = store.find_related("MOD-001")
        assert any(r.id == "SRC-001" for r in related)

    def test_find_related_covered_by(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="TC-001", aspect="implementation", element_type="test_case",
            title="Test", status=ElementStatus.DRAFT,
        ))
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Covered", status=ElementStatus.DRAFT,
            covered_by=["TC-001"],
        ))
        related = store.find_related("MOD-001")
        assert any(r.id == "TC-001" for r in related)

    def test_find_related_broken_link_skipped(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Module", status=ElementStatus.DRAFT,
            relationships={
                "depends_on": [RelationshipEntry(role="depends_on", target="MISSING-ID")],
            },
        ))
        related = store.find_related("MOD-001")
        assert all(r.id != "MISSING-ID" for r in related)


class TestFilesystemStorageParentSync:
    """Parent-child synchronization."""

    def test_write_adds_child_to_parent(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Parent", status=ElementStatus.DRAFT,
        ))
        store.write_element(Element(
            id="MOD-002", aspect="modules", element_type="component",
            title="Child", status=ElementStatus.DRAFT,
            parent="MOD-001",
        ))
        parent = store.read_element("MOD-001")
        assert "MOD-002" in parent.children

    def test_delete_removes_child_from_parent(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Parent", status=ElementStatus.DRAFT,
        ))
        store.write_element(Element(
            id="MOD-002", aspect="modules", element_type="component",
            title="Child", status=ElementStatus.DRAFT,
            parent="MOD-001",
        ))
        store.delete_element("MOD-002")
        parent = store.read_element("MOD-001")
        assert "MOD-002" not in parent.children

    def test_reparent_updates_both_parents(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Parent A", status=ElementStatus.DRAFT,
        ))
        store.write_element(Element(
            id="MOD-002", aspect="modules", element_type="module",
            title="Parent B", status=ElementStatus.DRAFT,
        ))
        store.write_element(Element(
            id="MOD-003", aspect="modules", element_type="component",
            title="Child", status=ElementStatus.DRAFT,
            parent="MOD-001",
        ))
        store.write_element(Element(
            id="MOD-003", aspect="modules", element_type="component",
            title="Child", status=ElementStatus.DRAFT,
            parent="MOD-002",
        ))
        parent_a = store.read_element("MOD-001")
        assert "MOD-003" not in parent_a.children
        parent_b = store.read_element("MOD-002")
        assert "MOD-003" in parent_b.children


class TestFilesystemStorageCycleDetection:
    """Cycle detection in parent chain."""

    def test_direct_self_cycle_raises(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Test", status=ElementStatus.DRAFT,
        ))
        with pytest.raises(ValueError, match="self"):
            store.write_element(Element(
                id="MOD-001", aspect="modules", element_type="module",
                title="Test", status=ElementStatus.DRAFT,
                parent="MOD-001",
            ))

    def test_indirect_cycle_raises(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="A", status=ElementStatus.DRAFT,
        ))
        store.write_element(Element(
            id="MOD-002", aspect="modules", element_type="module",
            title="B", status=ElementStatus.DRAFT,
            parent="MOD-001",
        ))
        with pytest.raises(ValueError, match="cycle"):
            store.write_element(Element(
                id="MOD-001", aspect="modules", element_type="module",
                title="A", status=ElementStatus.DRAFT,
                parent="MOD-002",
            ))

    def test_no_cycle_for_valid_parent(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Parent", status=ElementStatus.DRAFT,
        ))
        store.write_element(Element(
            id="MOD-002", aspect="modules", element_type="module",
            title="Child", status=ElementStatus.DRAFT,
            parent="MOD-001",
        ))
        store.write_element(Element(
            id="MOD-003", aspect="modules", element_type="module",
            title="Grandchild", status=ElementStatus.DRAFT,
            parent="MOD-002",
        ))
        assert store.read_element("MOD-003").parent == "MOD-002"


class TestFilesystemStorageReferenceValidation:
    """Reference validation for relationships, parent, etc."""

    def test_strict_refs_raises_on_broken_parent(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        el = Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Module", status=ElementStatus.DRAFT,
            parent="MISSING-PARENT",
        )
        with pytest.raises(ValueError, match="MISSING-PARENT"):
            store.write_element(el, strict_refs=True)

    def test_strict_refs_raises_on_broken_relationship(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        el = Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Module", status=ElementStatus.DRAFT,
            relationships={
                "depends_on": [RelationshipEntry(role="depends_on", target="MISSING")],
            },
        )
        with pytest.raises(ValueError, match="MISSING"):
            store.write_element(el, strict_refs=True)

    def test_non_strict_refs_does_not_raise(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        el = Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Module", status=ElementStatus.DRAFT,
            parent="MISSING-PARENT",
        )
        store.write_element(el, strict_refs=False)
        assert store.exists("MOD-001") is True


class TestFilesystemStorageDuplicateID:
    """Duplicate ID detection."""

    def test_update_same_id_no_error(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Original", status=ElementStatus.DRAFT,
        ))
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Updated", status=ElementStatus.CONFIRMED,
        ))
        assert store.read_element("MOD-001").title == "Updated"


class TestFilesystemStorageGetElementPath:
    """get_element_path functionality."""

    def test_get_element_path_returns_path(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Test", status=ElementStatus.DRAFT,
        ))
        path = store.get_element_path("MOD-001")
        assert path is not None
        assert "modules" in path
        assert "MOD-001" in path

    def test_get_element_path_missing_returns_none(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        assert store.get_element_path("NONEXISTENT") is None


class TestFilesystemStorageSubdirs:
    """Elements with parents go in subdirectories."""

    def test_element_with_parent_in_subdir(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Parent", status=ElementStatus.DRAFT,
        ))
        store.write_element(Element(
            id="MOD-001-C1", aspect="modules", element_type="component",
            title="Component", status=ElementStatus.DRAFT,
            parent="MOD-001",
        ))
        md_file = tmp_path / "aspects" / "modules" / "MOD-001" / "MOD-001-C1.md"
        assert md_file.is_file()

    def test_element_without_parent_not_in_subdir(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Standalone", status=ElementStatus.DRAFT,
        ))
        md_file = tmp_path / "aspects" / "modules" / "MOD-001.md"
        assert md_file.is_file()


class TestFilesystemStorageCleanup:
    """Cleanup of empty directories after delete."""

    def test_delete_cleans_empty_parent_dir(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Parent", status=ElementStatus.DRAFT,
        ))
        store.write_element(Element(
            id="MOD-001-C1", aspect="modules", element_type="component",
            title="Component", status=ElementStatus.DRAFT,
            parent="MOD-001",
        ))
        subdir = tmp_path / "aspects" / "modules" / "MOD-001"
        assert subdir.is_dir()
        store.delete_element("MOD-001-C1")
        assert not subdir.is_dir()

    def test_delete_last_element_in_subdir_cleans(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Parent", status=ElementStatus.DRAFT,
        ))
        store.write_element(Element(
            id="MOD-001-C1", aspect="modules", element_type="component",
            title="Only", status=ElementStatus.DRAFT,
            parent="MOD-001",
        ))
        subdir = tmp_path / "aspects" / "modules" / "MOD-001"
        assert subdir.is_dir()

        store.delete_element("MOD-001-C1")
        assert not subdir.is_dir() or not any(subdir.iterdir())


class TestFilesystemStorageEdgeCases:
    """Edge cases and error handling."""

    def test_write_empty_title(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="", status=ElementStatus.DRAFT,
        ))
        assert store.exists("MOD-001") is True

    def test_write_minimal_element(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        el = Element(id="X-001", aspect="test", element_type="test", title="Minimal")
        store.write_element(el)
        loaded = store.read_element("X-001")
        assert loaded.id == "X-001"
        assert loaded.status == ElementStatus.DRAFT
        assert loaded.children == []
        assert loaded.relationships == {}

    def test_write_and_read_multiple_aspects(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Module", status=ElementStatus.DRAFT,
        ))
        store.write_element(Element(
            id="UC-001", aspect="user_scenarios", element_type="use_case",
            title="Use Case", status=ElementStatus.DRAFT,
        ))
        store.write_element(Element(
            id="ENT-001", aspect="data_entities", element_type="entity",
            title="Entity", status=ElementStatus.DRAFT,
        ))
        assert store.count_all() == 3
        assert store.count_aspect("modules") == 1
        assert store.count_aspect("user_scenarios") == 1
        assert store.count_aspect("data_entities") == 1

    def test_different_project_paths_isolated(self, tmp_path: Path) -> None:
        p1 = tmp_path / "proj1"
        p2 = tmp_path / "proj2"
        s1 = FilesystemStorage(p1)
        s2 = FilesystemStorage(p2)

        s1.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Project 1", status=ElementStatus.DRAFT,
        ))
        s2.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Project 2", status=ElementStatus.DRAFT,
        ))

        assert s1.read_element("MOD-001").title == "Project 1"
        assert s2.read_element("MOD-001").title == "Project 2"

    def test_rebuild_index_after_external_write(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Original", status=ElementStatus.DRAFT,
        ))

        store2 = FilesystemStorage(tmp_path)
        assert store2.exists("MOD-001") is True
        assert store2.read_element("MOD-001").title == "Original"

    def test_write_with_tags(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Tagged", status=ElementStatus.DRAFT,
            tags=["important", "security"],
        ))
        loaded = store.read_element("MOD-001")
        assert "important" in loaded.tags
        assert "security" in loaded.tags

    def test_roundtrip_provenance(self, tmp_path: Path) -> None:
        from src.storage.models import Provenance
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="With Provenance", status=ElementStatus.DRAFT,
            provenance=Provenance(source="doc.txt", confidence=0.85),
        ))
        loaded = store.read_element("MOD-001")
        assert loaded.provenance is not None
        assert loaded.provenance.source == "doc.txt"
        assert loaded.provenance.confidence == 0.85

    def test_roundtrip_derived_from_covered_by(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Module", status=ElementStatus.DRAFT,
            derived_from=["SRC-001", "SRC-002"],
            covered_by=["TC-001"],
        ))
        loaded = store.read_element("MOD-001")
        assert "SRC-001" in loaded.derived_from
        assert "TC-001" in loaded.covered_by

    def test_relationship_entry_roundtrip(self, tmp_path: Path) -> None:
        store = FilesystemStorage(tmp_path)
        store.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Module", status=ElementStatus.DRAFT,
            relationships={
                "depends_on": [
                    RelationshipEntry(role="depends_on", target="LIB-001"),
                ],
                "applies_to": [
                    RelationshipEntry(role="applies_to", target="REQ-001"),
                ],
            },
        ))
        loaded = store.read_element("MOD-001")
        assert "depends_on" in loaded.relationships
        assert loaded.relationships["depends_on"][0].target == "LIB-001"
        assert "applies_to" in loaded.relationships
        assert loaded.relationships["applies_to"][0].target == "REQ-001"

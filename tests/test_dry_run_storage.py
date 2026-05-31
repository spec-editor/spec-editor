"""Tests for DryRunStorage adapter."""

from pathlib import Path

import pytest

from src.storage.dry_run import DryRunStorage
from src.storage.filesystem import FilesystemStorage
from src.storage.models import Element, ElementStatus


class TestDryRunStorage:
    """DryRunStorage reads from real, writes to temp."""

    @pytest.fixture
    def real_project(self, tmp_path: Path) -> Path:
        """Create a real project with one existing element."""
        real = tmp_path / "real"
        store = FilesystemStorage(real)
        store.write_element(Element(
            id="MOD-001",
            aspect="modules",
            element_type="module",
            title="Existing Module",
            status=ElementStatus.DRAFT,
        ))
        return real

    @pytest.fixture
    def temp_output(self, tmp_path: Path) -> Path:
        return tmp_path / "output"

    @pytest.fixture
    def storage(self, real_project: Path, temp_output: Path) -> DryRunStorage:
        return DryRunStorage(real_project, temp_output)

    # --- Read from real ---

    def test_reads_existing_element(self, storage: DryRunStorage) -> None:
        """Existing elements are readable from real storage."""
        element = storage.read_element("MOD-001")
        assert element.title == "Existing Module"

    def test_exists_for_real_element(self, storage: DryRunStorage) -> None:
        assert storage.exists("MOD-001") is True

    def test_list_all_includes_real(self, storage: DryRunStorage) -> None:
        elements = storage.list_all()
        assert len(elements) == 1
        assert elements[0].id == "MOD-001"

    # --- Write to temp, read merged ---

    def test_writes_to_temp_only(self, storage: DryRunStorage, real_project: Path,
                                  temp_output: Path) -> None:
        """New element is written to temp, not real project."""
        storage.write_element(Element(
            id="MOD-002",
            aspect="modules",
            element_type="module",
            title="New Module",
            status=ElementStatus.DRAFT,
        ))

        # Real storage should NOT have it
        real_store = FilesystemStorage(real_project)
        assert real_store.exists("MOD-002") is False

        # Temp storage should have it
        temp_store = FilesystemStorage(temp_output)
        assert temp_store.exists("MOD-002") is True

    def test_reads_merged_elements(self, storage: DryRunStorage) -> None:
        """list_all returns both real and temp elements."""
        storage.write_element(Element(
            id="MOD-002", aspect="modules", element_type="module",
            title="New", status=ElementStatus.DRAFT,
        ))

        all_el = storage.list_all()
        ids = {e.id for e in all_el}
        assert ids == {"MOD-001", "MOD-002"}

    def test_temp_overrides_real(self, storage: DryRunStorage) -> None:
        """If same ID exists in both, temp wins."""
        storage.write_element(Element(
            id="MOD-001", aspect="modules", element_type="module",
            title="Overridden Title", status=ElementStatus.CONFIRMED,
        ))

        element = storage.read_element("MOD-001")
        assert element.title == "Overridden Title"
        assert element.status == ElementStatus.CONFIRMED

    # --- Delete only from temp ---

    def test_delete_temp_element(self, storage: DryRunStorage) -> None:
        """Delete removes from temp but real element remains."""
        storage.write_element(Element(
            id="MOD-002", aspect="modules", element_type="module",
            title="To Delete", status=ElementStatus.DRAFT,
        ))
        assert storage.exists("MOD-002") is True

        storage.delete_element("MOD-002")
        assert storage.exists("MOD-002") is False  # MOD-002 was only in temp, now deleted

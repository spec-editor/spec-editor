"""Dry-run storage adapter — reads from real project, writes to temp dir."""

from pathlib import Path

from src.storage.adapter import StorageAdapter
from src.storage.filesystem import FilesystemStorage
from src.storage.models import Element, ElementSummary


class DryRunStorage(StorageAdapter):
    """Storage that writes to a temp directory while reading from the real project.

    Used by spec-editor run --dry-run to test agent output without
    modifying the real specification.
    """

    def __init__(self, project_path: Path, output_dir: Path) -> None:
        self._read_storage = FilesystemStorage(project_path)
        self._write_storage = FilesystemStorage(output_dir)
        self._output_dir = output_dir

    # ------------------------------------------------------------------
    # Read operations — delegate to real storage
    # ------------------------------------------------------------------

    def read_element(self, element_id: str) -> Element:
        # First try the write storage (newly created elements)
        if self._write_storage.exists(element_id):
            return self._write_storage.read_element(element_id)
        return self._read_storage.read_element(element_id)

    def list_aspect(self, aspect_name: str) -> list[ElementSummary]:
        real = {s.id: s for s in self._read_storage.list_aspect(aspect_name)}
        temp = {s.id: s for s in self._write_storage.list_aspect(aspect_name)}
        real.update(temp)  # temp overrides real for same IDs
        return list(real.values())

    def list_all(self) -> list[ElementSummary]:
        real = {s.id: s for s in self._read_storage.list_all()}
        temp = {s.id: s for s in self._write_storage.list_all()}
        real.update(temp)
        return list(real.values())

    def find_related(self, element_id: str) -> list[ElementSummary]:
        real = {s.id: s for s in self._read_storage.find_related(element_id)}
        temp = {s.id: s for s in self._write_storage.find_related(element_id)}
        real.update(temp)
        return list(real.values())

    def search(self, query: str) -> list[ElementSummary]:
        real = {s.id: s for s in self._read_storage.search(query)}
        temp = {s.id: s for s in self._write_storage.search(query)}
        real.update(temp)
        return list(real.values())

    def get_element_path(self, element_id: str) -> str | None:
        if self._write_storage.exists(element_id):
            return self._write_storage.get_element_path(element_id)
        return self._read_storage.get_element_path(element_id)

    def exists(self, element_id: str) -> bool:
        return self._read_storage.exists(element_id) or self._write_storage.exists(element_id)

    # ------------------------------------------------------------------
    # Write operations — only to temp storage
    # ------------------------------------------------------------------

    def write_element(self, element: Element) -> None:
        self._write_storage.write_element(element)

    def delete_element(self, element_id: str) -> None:
        if self._write_storage.exists(element_id):
            self._write_storage.delete_element(element_id)

    # ------------------------------------------------------------------
    # Dry-run specific
    # ------------------------------------------------------------------

    @property
    def output_dir(self) -> Path:
        return self._output_dir

"""Dry-run storage adapter — reads from real project, writes to temp dir."""

from pathlib import Path

from src.storage.adapter import StorageAdapter
from src.storage.filesystem import FilesystemStorage, element_to_summary
from src.storage.models import Element, ElementSummary
from src.tracing import StructuredLogEmitter, implements

log = StructuredLogEmitter(module_id="MOD-004")


@implements("REQ-005")
class DryRunStorage(StorageAdapter):
    """Storage that writes to a temp directory while reading from the real project.

    Used by spec-editor run --dry-run to test agent output without
    modifying the real specification.
    """

    def __init__(self, project_path: Path, output_dir: Path) -> None:
        try:
            self._read_storage = FilesystemStorage(project_path)
            self._write_storage = FilesystemStorage(output_dir)
        except Exception:
            log.exception("dry_run_init_failed", project_path=str(project_path), output_dir=str(output_dir))
            raise
        self._output_dir = output_dir
        log.info("dry_run_init", project_path=str(project_path), output_dir=str(output_dir))

    # ------------------------------------------------------------------
    # Read operations — delegate to real storage
    # ------------------------------------------------------------------

    def read_element(self, element_id: str) -> Element:
        # First try the write storage (newly created elements)
        if self._write_storage.exists(element_id):
            log.debug("dry_run_read_from_write", element_id=element_id)
            return self._write_storage.read_element(element_id)
        log.debug("dry_run_read_from_real", element_id=element_id)
        return self._read_storage.read_element(element_id)

    def list_aspect(
        self, aspect_name: str, offset: int = 0, limit: int = 0
    ) -> list[ElementSummary]:
        real = {s.id: s for s in self._read_storage.list_aspect(aspect_name)}
        temp = {s.id: s for s in self._write_storage.list_aspect(aspect_name)}
        real.update(temp)
        result = list(real.values())
        if offset:
            result = result[offset:]
        if limit:
            result = result[:limit]
        log.debug("dry_run_list_aspect", aspect=aspect_name, count=len(result))
        return result

    def list_all(self, offset: int = 0, limit: int = 0) -> list[ElementSummary]:
        real = {s.id: s for s in self._read_storage.list_all()}
        temp = {s.id: s for s in self._write_storage.list_all()}
        real.update(temp)
        result = list(real.values())
        if offset:
            result = result[offset:]
        if limit:
            result = result[:limit]
        log.debug("dry_run_list_all", count=len(result))
        return result

    def find_related(self, element_id: str) -> list[ElementSummary]:
        try:
            element = self.read_element(element_id)
        except KeyError:
            return []

        related_ids: set[str] = set()
        if element.parent:
            related_ids.add(element.parent)
        related_ids.update(element.children)
        for entries in element.relationships.values():
            for entry in entries:
                related_ids.add(entry.target)
        related_ids.update(element.derived_from)
        related_ids.update(element.covered_by)

        result: list[ElementSummary] = []
        for rid in related_ids:
            try:
                el = self.read_element(rid)
                result.append(element_to_summary(el))
            except KeyError:
                pass

        log.debug("dry_run_find_related", element_id=element_id, count=len(result))
        return result

    def count_all(self) -> int:
        """Total element count (merged from both storages)."""
        return len(self.list_all())

    def count_aspect(self, aspect_name: str) -> int:
        """Element count for a specific aspect (merged, deduplicated by ID)."""
        real = {s.id for s in self._read_storage.list_aspect(aspect_name)}
        temp = {s.id for s in self._write_storage.list_aspect(aspect_name)}
        return len(real | temp)

    def search(
        self, query: str, offset: int = 0, limit: int = 0
    ) -> list[ElementSummary]:
        real = {s.id: s for s in self._read_storage.search(query)}
        temp = {s.id: s for s in self._write_storage.search(query)}
        real.update(temp)
        result = list(real.values())
        if offset:
            result = result[offset:]
        if limit:
            result = result[:limit]
        log.debug("dry_run_search", query=query, count=len(result))
        return result

    def get_element_path(self, element_id: str) -> str | None:
        if self._write_storage.exists(element_id):
            return self._write_storage.get_element_path(element_id)
        return self._read_storage.get_element_path(element_id)

    def exists(self, element_id: str) -> bool:
        exists = self._read_storage.exists(element_id) or self._write_storage.exists(element_id)
        log.debug("dry_run_exists", element_id=element_id, exists=exists)
        return exists

    # ------------------------------------------------------------------
    # Write operations — only to temp storage
    # ------------------------------------------------------------------

    def write_element(self, element: Element) -> None:
        log.info("dry_run_write_element", element_id=element.id, title=element.title)
        try:
            self._write_storage.write_element(element)
        except Exception:
            log.exception("dry_run_write_element_failed", element_id=element.id)
            raise

    def delete_element(self, element_id: str) -> None:
        if self._write_storage.exists(element_id):
            log.info("dry_run_delete_element", element_id=element_id)
            try:
                self._write_storage.delete_element(element_id)
            except Exception:
                log.exception("dry_run_delete_element_failed", element_id=element_id)
                raise

    # ------------------------------------------------------------------
    # Dry-run specific
    # ------------------------------------------------------------------

    def _rebuild_index(self) -> None:
        """Rebuild internal indexes in both storages.

        Called by list_all_elements tool to pick up disk changes
        from external tools (reengineer CLI, etc.).
        """
        self._read_storage._rebuild_index()
        self._write_storage._rebuild_index()

    @property
    def output_dir(self) -> Path:
        return self._output_dir

"""Dry‐run storage that wraps two FilesystemStorage instances.

Reads merge own writes with the original project.
Writes go to a separate .dry_run/ directory.
Enables testing modifications without altering the real specification.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .filesystem_storage import FilesystemStorage

logger = logging.getLogger(__name__)


class DryRunStorage:
    """Wraps a read‐only storage (real project) and a write‐storage (.dry_run/).

    Reads check the write storage first, falling back to the read storage.
    All mutation operations are forwarded exclusively to the write storage,
    leaving the original project untouched.
    """

    def __init__(
        self,
        project_path: Path,
        dry_run_path: Optional[Path] = None,
    ) -> None:
        if dry_run_path is None:
            dry_run_path = project_path / ".dry_run"
        self._read_storage = FilesystemStorage(project_path)
        self._write_storage = FilesystemStorage(dry_run_path)
        self._dry_run_path = dry_run_path
        logger.info("DryRunStorage initialized: read=%s, write=%s", project_path, dry_run_path)

    # ---------- read operations ----------
    def read_element(self, element_id: str) -> Optional[Dict[str, Any]]:
        element = self._write_storage.read_element(element_id)
        if element is None:
            logger.debug("read_element: %s from read storage", element_id)
            element = self._read_storage.read_element(element_id)
        else:
            logger.debug("read_element: %s from write storage", element_id)
        return element

    def list_all_elements(self) -> List[Dict[str, Any]]:
        read_ids: Set[str] = set()
        for el in self._read_storage.list_all_elements():
            read_ids.add(el.get("element_id", ""))
        # start with write elements (they take precedence)
        result = list(self._write_storage.list_all_elements())
        seen_ids = {el.get("element_id", "") for el in result}
        for el_read in self._read_storage.list_all_elements():
            eid = el_read.get("element_id", "")
            if eid not in seen_ids:
                result.append(el_read)
                seen_ids.add(eid)
        return result

    def search_elements(self, query: str) -> List[Dict[str, Any]]:
        read_results = self._read_storage.search_elements(query)
        write_results = self._write_storage.search_elements(query)
        write_ids = {el.get("element_id", "") for el in write_results}
        merged = [el for el in read_results if el.get("element_id", "") not in write_ids]
        merged.extend(write_results)
        return merged

    # ---------- write operations ----------
    def write_element(self, element_id: str, content: Dict[str, Any]) -> None:
        logger.info("write_element: %s (dry-run)", element_id)
        self._write_storage.write_element(element_id, content)

    def delete_element(self, element_id: str) -> None:
        logger.info("delete_element: %s (dry-run)", element_id)
        self._write_storage.delete_element(element_id)

    def add_relationship(
        self, element_id: str, relationship: Dict[str, Any]
    ) -> None:
        logger.info("add_relationship: %s (dry-run)", element_id)
        self._write_storage.add_relationship(element_id, relationship)

    def remove_relationship(
        self, element_id: str, relationship_id: str
    ) -> None:
        logger.info("remove_relationship: %s rel=%s (dry-run)", element_id, relationship_id)
        self._write_storage.remove_relationship(element_id, relationship_id)

    # ---------- dry-run specific ----------
    @property
    def output_dir(self) -> Path:
        return self._dry_run_path

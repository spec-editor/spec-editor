"""Minimal in-memory implementation of FilesystemStorage.

Used for testing.  Stores elements in a dictionary.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional


class FilesystemStorage:
    """Stores specification elements in memory (not on disk)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._elements: Dict[str, Dict[str, Any]] = {}

    # ---------- read operations ----------
    def read_element(self, element_id: str) -> Optional[Dict[str, Any]]:
        return self._elements.get(element_id)

    def list_all_elements(self) -> List[Dict[str, Any]]:
        return list(self._elements.values())

    def search_elements(self, query: str) -> List[Dict[str, Any]]:
        return [
            el for el in self._elements.values()
            if query in str(el)
        ]

    # ---------- write operations ----------
    def write_element(self, element_id: str, content: Dict[str, Any]) -> None:
        self._elements[element_id] = content

    def delete_element(self, element_id: str) -> None:
        self._elements.pop(element_id, None)

    def add_relationship(
        self, element_id: str, relationship: Dict[str, Any]
    ) -> None:
        el = self._elements.get(element_id)
        if el is None:
            raise ValueError(
                f"Element {element_id!r} not found"
            )
        if "relationships" not in el:
            el["relationships"] = []
        el["relationships"].append(relationship)

    def remove_relationship(
        self, element_id: str, relationship_id: str
    ) -> None:
        el = self._elements.get(element_id)
        if el is None:
            raise ValueError(
                f"Element {element_id!r} not found"
            )
        rels = el.get("relationships", [])
        # Remove the first relationship whose "id" equals relationship_id.
        el["relationships"] = [
            r for r in rels
            if r.get("id") != relationship_id
        ]

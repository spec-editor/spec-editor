"""Filesystem storage implementation (.md + YAML frontmatter)."""

import os
import tempfile
from pathlib import Path

from src.config import get_logger
from src.storage.adapter import StorageAdapter
from src.storage.models import Element, ElementSummary, element_to_summary
from src.storage.parser import parse_md_file, write_md_file

logger = get_logger(__name__)


class FilesystemStorage(StorageAdapter):
    """Element storage as .md files in the aspects/ folder.

    Structure:
        <project_root>/aspects/
        ├── modules/
        │   ├── MOD-001.md
        │   └── components/
        │       └── MOD-001-C1.md
        ├── user_scenarios/
        └── ...
    """

    def __init__(self, project_path: Path) -> None:
        self._project_path = Path(project_path)
        self._aspects_path = self._project_path / "aspects"

        # Cache: element id -> relative path to .md file
        self._index: dict[str, Path] = {}
        self._rebuild_index()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def read_element(self, element_id: str) -> Element:
        """Read a full element by ID.

        Raises:
            KeyError: element not found
        """
        path = self._resolve_path(element_id)
        return parse_md_file(path)

    def write_element(self, element: Element, strict_refs: bool = False) -> None:
        """Create or update an element."""
        old_path = self._index.get(element.id)

        # Check: cannot create an element with an ID that is already taken by another file
        if old_path is None and self.exists(element.id):
            raise ValueError(
                f"Element with ID '{element.id}' already exists. "
                f"Use a different ID or delete the existing one."
            )

        self._index.pop(element.id, None)

        # Reference validation
        self._validate_references(element, strict=strict_refs)

        if old_path:
            path = self._aspects_path / old_path
        else:
            path = self._aspects_path / self._make_path(element)

        # Atomic write: temp file + rename
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd, tmp_path = tempfile.mkstemp(
                suffix=".md",
                prefix="spec-",
                dir=str(path.parent),
            )
            try:
                write_md_file(Path(tmp_path), element)
            finally:
                os.close(fd)
            os.replace(tmp_path, path)
        except Exception:
            # On error, delete temp file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

        # Update the index
        self._index[element.id] = path

        # Sync children with the parent
        if element.parent:
            self._sync_parent_children(element.id, element.parent)

        logger.debug("write_element", element_id=element.id, path=str(path))

    def delete_element(self, element_id: str) -> None:
        """Delete an element by ID."""
        path = self._resolve_path(element_id)

        # Remove from parent's children
        try:
            element = parse_md_file(self._aspects_path / path)
            if element.parent:
                self._remove_from_parent_children(element.id, element.parent)
        except Exception:
            pass  # parent may have already been deleted

        # Delete the file
        path.unlink(missing_ok=True)
        self._index.pop(element_id, None)

        # Remove empty parent directories
        self._cleanup_empty_dirs(path.parent)

        logger.debug("delete_element", element_id=element_id)

    def list_aspect(self, aspect_name: str) -> list[ElementSummary]:
        """List all elements in an aspect (summary form)."""
        aspect_dir = self._aspects_path / aspect_name
        if not aspect_dir.is_dir():
            return []

        result: list[ElementSummary] = []
        for md_file in aspect_dir.rglob("*.md"):
            try:
                element = parse_md_file(md_file)
                result.append(element_to_summary(element))
            except Exception as exc:
                logger.warning(
                    "skip_invalid_file",
                    path=str(md_file),
                    error=str(exc),
                )
        return result

    def list_all(self) -> list[ElementSummary]:
        """List all project elements (summary form)."""
        if not self._aspects_path.is_dir():
            return []

        result: list[ElementSummary] = []
        for md_file in self._aspects_path.rglob("*.md"):
            try:
                element = parse_md_file(md_file)
                result.append(element_to_summary(element))
            except Exception as exc:
                logger.warning(
                    "skip_invalid_file",
                    path=str(md_file),
                    error=str(exc),
                )
        return result

    def find_related(self, element_id: str) -> list[ElementSummary]:
        """Find all elements related to this one."""
        try:
            element = self.read_element(element_id)
        except KeyError:
            return []

        related_ids: set[str] = set()

        # Parent
        if element.parent:
            related_ids.add(element.parent)

        # Children
        related_ids.update(element.children)

        # Typed relationships
        for entries in element.relationships.values():
            for entry in entries:
                related_ids.add(entry.target)

        # derived_from / covered_by
        related_ids.update(element.derived_from)
        related_ids.update(element.covered_by)

        result: list[ElementSummary] = []
        for rid in related_ids:
            try:
                result.append(element_to_summary(self.read_element(rid)))
            except KeyError:
                pass  # broken link — skip

        return result

    def search(self, query: str) -> list[ElementSummary]:
        """Full-text search across ID, title and content."""
        if not query:
            return []

        query_lower = query.lower()
        result: list[ElementSummary] = []

        for element_id, path in self._index.items():
            try:
                element = parse_md_file(self._aspects_path / path)
            except Exception:
                continue

            # Search in ID, title, content
            if (
                query_lower in element.id.lower()
                or query_lower in element.title.lower()
                or query_lower in element.content.lower()
            ):
                result.append(element_to_summary(element))

        return result

    def get_element_path(self, element_id: str) -> str | None:
        """Get the file path of an element."""
        path = self._index.get(element_id)
        return str(path) if path else None

    def exists(self, element_id: str) -> bool:
        """Check if an element exists."""
        return element_id in self._index

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    def _validate_references(self, element: Element, strict: bool = False) -> None:
        """Validate element references against existing target elements.

        When strict=True, raises ValueError on broken references.
         When strict=False, logs a warning instead.
        """
        refs_to_check: list[tuple[str, str]] = []

        if element.parent:
            refs_to_check.append(("parent", element.parent))

        for child_id in element.children:
            refs_to_check.append(("children", child_id))

        for rel_type, entries in element.relationships.items():
            for entry in entries:
                refs_to_check.append((f"relationships.{rel_type}", entry.target))

        for derived_id in element.derived_from:
            refs_to_check.append(("derived_from", derived_id))

        for covered_id in element.covered_by:
            refs_to_check.append(("covered_by", covered_id))

        for field, target_id in refs_to_check:
            if not self.exists(target_id):
                msg = (
                    f" '{element.id}':  '{field}' "
                    f"agent limit reached  '{target_id}'"
                )
                if strict:
                    raise ValueError(msg)
                else:
                    logger.warning(
                        "dangling_reference",
                        **{
                            "element_id": element.id,
                            "field": field,
                            "target_id": target_id,
                        },
                    )

    def _resolve_path(self, element_id: str) -> Path:
        """Get the absolute path to the element's .md file.

        Raises:
            KeyError: element not found in 
        """
        rel_path = self._index.get(element_id)
        if rel_path is None:
            # Possibly the index is stale — rebuild
            self._rebuild_index()
            rel_path = self._index.get(element_id)
            if rel_path is None:
                raise KeyError(
                    f"Element not found: {element_id}. "
                    f"Check the folder {self._aspects_path}"
                )
        return self._aspects_path / rel_path

    def _make_path(self, element: Element) -> Path:
        """Determine the path for a new element based on aspect and parent."""
        base = self._aspects_path / element.aspect

        if element.parent and element.parent in self._index:
            # Place next to parent or in its subfolder
            parent_path = self._index[element.parent]
            parent_dir = parent_path.parent
            # If parent is file MOD-001.md, place in MOD-001/
            if parent_path.stem == element.parent:
                base = parent_dir / element.parent
            else:
                base = parent_dir

        return base / f"{element.id}.md"

    def _rebuild_index(self) -> None:
        """Rebuild the id -> relative path index."""
        self._index.clear()

        if not self._aspects_path.is_dir():
            return

        for md_file in self._aspects_path.rglob("*.md"):
            try:
                element = parse_md_file(md_file)
                rel_path = md_file.relative_to(self._aspects_path)
                self._index[element.id] = rel_path
            except Exception as exc:
                logger.warning(
                    "skip_invalid_file",
                    path=str(md_file),
                    error=str(exc),
                )

        logger.debug("index_rebuilt", count=len(self._index))

    def _sync_parent_children(self, child_id: str, parent_id: str) -> None:
        """Add child_id to parent's children (if not already there)."""
        try:
            parent = self.read_element(parent_id)
        except KeyError:
            return  # parent not yet created — ok

        if child_id not in parent.children:
            parent.children.append(child_id)
            # Write without recursive sync
            parent_path = self._resolve_path(parent_id)
            write_md_file(parent_path, parent)

    def _remove_from_parent_children(self, child_id: str, parent_id: str) -> None:
        """Remove child_id from parent's children."""
        try:
            parent = self.read_element(parent_id)
        except KeyError:
            return

        if child_id in parent.children:
            parent.children.remove(child_id)
            parent_path = self._resolve_path(parent_id)
            write_md_file(parent_path, parent)

    @staticmethod
    def _cleanup_empty_dirs(directory: Path) -> None:
        """Remove empty parent directories (up to aspects/)."""
        for _ in range(10):  # guard against infinite loop
            if not directory.is_dir():
                break
            try:
                if any(directory.iterdir()):
                    break
                directory.rmdir()
                directory = directory.parent
            except OSError:
                break

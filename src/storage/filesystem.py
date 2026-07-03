"""Filesystem storage implementation (.md + YAML frontmatter)."""

import concurrent.futures
import os
import tempfile
from pathlib import Path

import yaml

from src.agents.events import get_event_bus
from src.config import get_logger
from src.storage.adapter import StorageAdapter
from src.storage.models import (
    Element,
    ElementStatus,
    ElementSummary,
    RelationshipEntry,
    element_to_summary,
)
from src.storage.parser import parse_md_file, write_md_file
from src.tracing import implements

logger = get_logger(__name__)

# Maximum worker threads for parallel index rebuild
_REBUILD_MAX_WORKERS = 8

# Class-level cache: project_path -> EventBus
# Avoids recreating Redis connections and reparsing YAML on every write.
_event_bus_cache: dict[str, "EventBus"] = {}


def _fast_parse_file(
    md_file: Path,
) -> tuple[str, Path, str, str, ElementSummary, str] | None:
    """Fast file parsing for index rebuild.

    Uses direct YAML parsing instead of the heavier frontmatter library,
    and constructs ElementSummary directly without Pydantic validation.
    Returns (element_id, rel_path, aspect, title, summary, search_text) or None.
    """
    try:
        text = md_file.read_text(encoding="utf-8")
    except Exception:
        return None

    content = ""
    fm: dict = {}
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                fm = yaml.safe_load(parts[1]) or {}
            except Exception:
                pass
            content = (parts[2] or "").strip()
    elif text.startswith("+++"):
        parts = text.split("+++", 2)
        if len(parts) >= 3:
            try:
                fm = yaml.safe_load(parts[1]) or {}
            except Exception:
                pass
            content = (parts[2] or "").strip()

    element_id = fm.get("id", "")
    if not element_id:
        return None

    aspect = fm.get("aspect", "")
    title = fm.get("title", "")
    element_type = fm.get("element_type", "")
    status_raw = fm.get("status", "draft")
    parent = fm.get("parent")
    children = fm.get("children", [])
    tags = fm.get("tags", [])

    relationships_raw = fm.get("relationships", {})
    relationships: dict[str, list[RelationshipEntry]] = {}
    if relationships_raw:
        for rel_type, entries in relationships_raw.items():
            if isinstance(entries, list):
                rel_list = []
                for entry in entries:
                    if isinstance(entry, dict):
                        rel_list.append(
                            RelationshipEntry.model_construct(
                                role=entry.get("role", rel_type),
                                target=entry.get("target", ""),
                            )
                        )
                    elif isinstance(entry, str):
                        rel_list.append(
                            RelationshipEntry.model_construct(
                                role=rel_type, target=entry
                            )
                        )
                if rel_list:
                    relationships[rel_type] = rel_list

    try:
        status = ElementStatus(status_raw)
    except ValueError:
        status = ElementStatus.DRAFT

    summary = ElementSummary.model_construct(
        aspect=aspect,
        element_type=element_type,
        id=element_id,
        title=title,
        status=status,
        parent=parent,
        children=children if isinstance(children, list) else [],
        relationships=relationships,
        tags=tags if isinstance(tags, list) else [],
    )

    search_text = " ".join([element_id, title or "", content]).lower()
    rel_path = md_file.relative_to(md_file.parent.parent)
    return (element_id, rel_path, aspect, title, summary, search_text)


def _fast_parse_file_with_base(
    args: tuple[Path, Path],
) -> tuple[str, Path, str, str, ElementSummary, str] | None:
    """Wrapper: parses file with explicit base path for relative path calculation."""
    md_file, base_path = args
    result = _fast_parse_file(md_file)
    if result is None:
        return None
    element_id, _, aspect, title, summary, search_text = result
    # Correctly compute path relative to aspects base
    rel_path = md_file.relative_to(base_path)
    return (element_id, rel_path, aspect, title, summary, search_text)


def _get_cached_event_bus(project_path: str) -> "EventBus | None":
    """Get or create a cached EventBus for the given project path."""
    cached = _event_bus_cache.get(project_path)
    if cached is not None:
        return cached
    try:
        bus = get_event_bus(project_path)
        _event_bus_cache[project_path] = bus
        return bus
    except Exception:
        return None


@implements("CA-002")
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
        # Cache: element id -> ElementSummary (avoids re-parsing files for lists)
        self._summary_cache: dict[str, ElementSummary] = {}
        # Cache: element id -> full searchable text (id + title + content)
        self._search_cache: dict[str, str] = {}
        # Cache: aspect -> {lowercase_title: element_id} for O(1) dedup lookup
        self._title_cache: dict[str, dict[str, str]] = {}
        # Cache: aspect -> set of element_ids for O(1) aspect queries
        self._aspect_index: dict[str, set[str]] = {}
        self._rebuild_index()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @implements("REQ-003")
    def read_element(self, element_id: str) -> Element:
        """Read a full element by ID.

        Raises:
            KeyError: element not found
        """
        path = self._resolve_path(element_id)
        return parse_md_file(path)

    @implements("REQ-003")
    def write_element(self, element: Element, strict_refs: bool = False) -> None:
        """Create or update an element.

        When updating an existing element (same ID), only the fields explicitly
        set in the incoming element are updated — existing relationships,
        children, etc. are preserved. This prevents accidental data loss
        when agents update just the status field.
        """
        old_path = self._index.get(element.id)
        old_parent: str | None = None

        # Merge with existing element if updating
        if old_path is not None:
            try:
                existing = parse_md_file(self._aspects_path / old_path)
                old_parent = existing.parent
                # Incoming explicitly set relationships (even if empty — agent
                # cleared them via remove_relationship). Only preserve existing
                # when relationships field is genuinely absent (None).
                if element.relationships is not None:
                    pass  # use incoming as-is
                elif existing.relationships:
                    element.relationships = existing.relationships
                # Preserve children if incoming has none (None); overwrite if provided
                if element.children is not None:
                    pass  # use incoming as-is (even if empty [])
                elif existing.children:
                    element.children = existing.children
                # Preserve parent if incoming has none, but respect explicit parent=null
                if not element.parent and existing.parent:
                    element.parent = existing.parent
                # Preserve content if incoming is empty, else prefer longer
                if not element.content and existing.content:
                    element.content = existing.content
                elif (
                    element.content
                    and existing.content
                    and len(existing.content) > len(element.content)
                ):
                    element.content = existing.content
                # Preserve title if incoming is empty
                if not element.title and existing.title:
                    element.title = existing.title
            except Exception:
                pass  # If we can't read existing, write the new one as-is

        # Check: cannot create an element with an ID that is already taken by another file
        if old_path is None and self.exists(element.id):
            raise ValueError(
                f"Element with ID '{element.id}' already exists. "
                f"Use a different ID or delete the existing one."
            )

        # Deduplication: if creating a new element (no old_path), check for existing
        # element with same title in same aspect (case-insensitive). If found, merge.
        if old_path is None:
            existing_id = self._find_by_title(element.aspect, element.title)
            if existing_id and existing_id != element.id:
                try:
                    dup = parse_md_file(self._aspects_path / self._index[existing_id])
                    # Merge: content (longer wins), relationships (union),
                    # children (union), derived_from (union)
                    if element.content and len(element.content) > len(
                        dup.content or ""
                    ):
                        dup.content = element.content
                    if element.relationships:
                        for rel_type, targets in element.relationships.items():
                            existing_targets = {
                                e.target for e in dup.relationships.get(rel_type, [])
                            }
                            new_targets = {
                                e.target if hasattr(e, "target") else str(e)
                                for e in (
                                    targets if isinstance(targets, list) else [targets]
                                )
                            }
                            merged = sorted(existing_targets | new_targets)
                            dup.relationships[rel_type] = [
                                RelationshipEntry(role=rel_type, target=t)
                                for t in merged
                            ]
                    if element.children:
                        dup.children = sorted(
                            set(dup.children or []) | set(element.children)
                        )
                    if element.derived_from:
                        dup.derived_from = sorted(
                            set(dup.derived_from or []) | set(element.derived_from)
                        )
                    self._index.pop(dup.id, None)
                    path = self._aspects_path / self._index.get(
                        dup.id, self._make_path(dup)
                    )
                    # Use atomic write for the merged element
                    path.parent.mkdir(parents=True, exist_ok=True)
                    fd, tmp_path = tempfile.mkstemp(
                        suffix=".md", prefix="spec-", dir=str(path.parent)
                    )
                    try:
                        write_md_file(Path(tmp_path), dup)
                    finally:
                        os.close(fd)
                    os.replace(tmp_path, path)
                    self._index[dup.id] = self._make_path(dup).relative_to(
                        self._aspects_path
                    )
                    # Update caches after dedup merge
                    self._summary_cache[dup.id] = element_to_summary(dup)
                    self._aspect_index.setdefault(dup.aspect, set()).add(dup.id)
                    self._search_cache[dup.id] = " ".join(
                        [
                            dup.id or "",
                            dup.title or "",
                            dup.content or "",
                        ]
                    ).lower()
                    self._update_title_cache(dup.id, dup.aspect, dup.title)
                    logger.info(
                        "dedup_merged",
                        new_title=element.title,
                        into=dup.id,
                        aspect=element.aspect,
                    )
                    # Fire-and-forget event publish
                    try:
                        bus = _get_cached_event_bus(str(self._project_path))
                        if bus:
                            bus.publish(
                                "elements:changed",
                                {
                                    "action": "write",
                                    "elementId": dup.id,
                                    "aspect": dup.aspect,
                                },
                            )
                    except Exception:
                        pass
                    return  # Merged — don't create duplicate
                except Exception as exc:
                    logger.warning("dedup_merge_error", error=str(exc))
                    # Fall through to create new element

        self._index.pop(element.id, None)

        # Reference validation
        self._validate_references(element, strict=strict_refs)

        # Cycle detection: walk parent chain instead of loading all elements
        if element.parent:
            cycle_msg = self._check_parent_cycle(element.id, element.parent)
            if cycle_msg:
                raise ValueError(cycle_msg)

        if old_path:
            path = self._aspects_path / old_path
        else:
            # _make_path returns relative path from _aspects_path
            path = self._make_path(element)

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

        # Update the index with relative path
        self._index[element.id] = path.relative_to(self._aspects_path)

        # Update caches
        self._summary_cache[element.id] = element_to_summary(element)
        self._aspect_index.setdefault(element.aspect, set()).add(element.id)
        self._search_cache[element.id] = " ".join(
            [
                element.id or "",
                element.title or "",
                element.content or "",
            ]
        ).lower()
        self._update_title_cache(element.id, element.aspect, element.title)

        # Sync parent: remove from old parent if parent changed, add to new parent
        # Note: old_parent was captured during merge phase (before file write)
        if element.parent and element.parent != old_parent:
            self._sync_parent_children(element.id, element.parent)
        if old_parent and old_parent != element.parent:
            self._remove_from_parent_children(element.id, old_parent)

        # Fire-and-forget event publish
        try:
            bus = _get_cached_event_bus(str(self._project_path))
            if bus:
                bus.publish(
                    "elements:changed",
                    {
                        "action": "write",
                        "elementId": element.id,
                        "aspect": element.aspect,
                    },
                )
        except Exception:
            pass

        logger.debug("write_element", element_id=element.id, path=str(path))

    def delete_element(self, element_id: str) -> None:
        """Delete an element by ID."""
        path = self._resolve_path(element_id)

        # Remove from parent's children
        element = None
        try:
            element = parse_md_file(self._aspects_path / path)
            if element.parent:
                self._remove_from_parent_children(element.id, element.parent)
        except Exception:
            pass  # parent may have already been deleted

        # Delete the file
        path.unlink(missing_ok=True)
        self._index.pop(element_id, None)
        self._summary_cache.pop(element_id, None)
        self._search_cache.pop(element_id, None)
        if element:
            self._remove_title_cache(element.id, element.aspect, element.title)
            aspect_set = self._aspect_index.get(element.aspect)
            if aspect_set:
                aspect_set.discard(element_id)

        # Remove empty parent directories
        self._cleanup_empty_dirs(path.parent)

        # Remove dangling references from other elements
        # (derived_from, relationships, children that point to the deleted element)
        if element:
            removed_count = self._cleanup_dangling_references(element_id)
            if removed_count > 0:
                logger.debug(
                    "delete_element_cleanup",
                    element_id=element_id,
                    dangling_refs_removed=removed_count,
                )

        # Fire-and-forget event publish
        try:
            bus = _get_cached_event_bus(str(self._project_path))
            if bus:
                bus.publish(
                    "elements:changed",
                    {
                        "action": "delete",
                        "elementId": element_id,
                        "aspect": element.aspect if element else "",
                        "danglingRefsRemoved": removed_count if element else 0,
                    },
                )
        except Exception:
            pass

        logger.debug("delete_element", element_id=element_id)

    @implements("REQ-003")
    def count_aspect(self, aspect_name: str) -> int:
        """Get element count for a specific aspect in O(1)."""
        return len(self._aspect_index.get(aspect_name, set()))

    @implements("REQ-003")
    def list_aspect(
        self, aspect_name: str, offset: int = 0, limit: int = 0
    ) -> list[ElementSummary]:
        """List all elements in an aspect (summary form)."""
        aspect_ids = self._aspect_index.get(aspect_name)
        if aspect_ids is None:
            return []
        result = [
            self._summary_cache[eid] for eid in aspect_ids if eid in self._summary_cache
        ]
        if offset:
            result = result[offset:]
        if limit:
            result = result[:limit]
        return result

    @implements("REQ-003")
    def list_all(self, offset: int = 0, limit: int = 0) -> list[ElementSummary]:
        """List all project elements (summary form)."""
        result = list(self._summary_cache.values())
        if offset:
            result = result[offset:]
        if limit:
            result = result[:limit]
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
            summary = self._summary_cache.get(rid)
            if summary is not None:
                result.append(summary)
            else:
                try:
                    result.append(element_to_summary(self.read_element(rid)))
                except KeyError:
                    pass  # broken link — skip

        return result

    @implements("REQ-003")
    def search(
        self, query: str, offset: int = 0, limit: int = 0
    ) -> list[ElementSummary]:
        """Full-text search across ID, title and content."""
        if not query:
            return []

        query_lower = query.lower()
        result: list[ElementSummary] = []

        for element_id, searchable_text in self._search_cache.items():
            if query_lower in searchable_text:
                summary = self._summary_cache.get(element_id)
                if summary is not None:
                    result.append(summary)

        if offset:
            result = result[offset:]
        if limit:
            result = result[:limit]
        return result

    def get_element_path(self, element_id: str) -> str | None:
        """Get the file path of an element."""
        path = self._index.get(element_id)
        return str(path) if path else None

    @implements("REQ-003")
    def count_all(self) -> int:
        """Get total element count without loading summaries (scalability)."""
        return len(self._summary_cache)

    @implements("REQ-003")
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
                msg = f" '{element.id}':  '{field}' agent limit reached  '{target_id}'"
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
        """Determine the absolute path for a new element based on aspect and parent.

        When the element has a parent in the index, derives the aspect directory
        from the parent's path — this prevents LLM agents from accidentally using
        a parent ID as the aspect directory (e.g. aspect="SCR-001" → aspect="scenarios").
        """
        if element.parent and element.parent in self._index:
            # Place inside parent's subfolder, deriving aspect from parent's location
            parent_rel = self._index[element.parent]
            # parent_rel is like "scenarios/SCR-001.md" → parent dir = "scenarios"
            aspect_dir = parent_rel.parent  # "scenarios"
            if parent_rel.stem == element.parent:
                base = self._aspects_path / aspect_dir / element.parent
            else:
                base = self._aspects_path / aspect_dir
        else:
            base = self._aspects_path / element.aspect

        return base / f"{element.id}.md"

    def _find_by_title(self, aspect: str, title: str) -> str | None:
        """Find element ID by title within an aspect. Case-insensitive.

        Uses the title cache for O(1) lookup.
        Returns the ID of the first matching element, or None if not found.
        Used for deduplication when two agents create elements with the same title.
        """
        if not title:
            return None
        title_lower = title.strip().lower()
        aspect_cache = self._title_cache.get(aspect)
        if aspect_cache is not None:
            return aspect_cache.get(title_lower)
        # Fallback: scan (should not happen if caches are maintained)
        for summary in self.list_aspect(aspect):
            if summary.title.strip().lower() == title_lower:
                return summary.id
        return None

    def _update_title_cache(self, element_id: str, aspect: str, title: str) -> None:
        """Update the title cache for a given element."""
        if not title:
            return
        title_lower = title.strip().lower()
        if aspect not in self._title_cache:
            self._title_cache[aspect] = {}
        self._title_cache[aspect][title_lower] = element_id

    def _remove_title_cache(self, element_id: str, aspect: str, title: str) -> None:
        """Remove an entry from the title cache."""
        if not title:
            return
        title_lower = title.strip().lower()
        aspect_cache = self._title_cache.get(aspect)
        if aspect_cache and aspect_cache.get(title_lower) == element_id:
            del aspect_cache[title_lower]

    @implements("REQ-003")
    def _rebuild_index(self) -> None:
        """Rebuild the id -> relative path index and caches.

        Uses parallel fast-path YAML parsing (avoids frontmatter library overhead)
        and model_construct (skips Pydantic validation) for 1000+ files (REQ-003).
        """
        self._index.clear()
        self._summary_cache.clear()
        self._search_cache.clear()
        self._title_cache.clear()
        self._aspect_index.clear()

        if not self._aspects_path.is_dir():
            return

        md_files = list(self._aspects_path.rglob("*.md"))
        # Pass (file, base_path) tuples so relative paths are computed correctly
        args = [(f, self._aspects_path) for f in md_files]
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=_REBUILD_MAX_WORKERS
        ) as executor:
            results = list(executor.map(_fast_parse_file_with_base, args))

        for result in results:
            if result is None:
                continue
            element_id, rel_path, aspect, title, summary, search_text = result
            self._index[element_id] = rel_path
            self._summary_cache[element_id] = summary
            self._search_cache[element_id] = search_text
            self._update_title_cache(element_id, aspect, title)
            if aspect:
                self._aspect_index.setdefault(aspect, set()).add(element_id)

        logger.debug("index_rebuilt", count=len(self._index))

    def _check_parent_cycle(self, element_id: str, parent_id: str | None) -> str | None:
        """Check if setting element_id.parent = parent_id would create a cycle.

        Walks upward from parent_id through parents using the summary cache.
        Returns error message if cycle is detected, else None.
        """
        if not parent_id:
            return None
        if parent_id == element_id:
            return f"Cannot set parent to self: '{element_id}' → '{parent_id}'"

        visited = {element_id}
        current = parent_id
        while current:
            if current in visited:
                chain = " → ".join(visited | {current})
                return f"Parent cycle detected: {chain}"
            visited.add(current)
            summary = self._summary_cache.get(current)
            if summary is None:
                break
            current = summary.parent
        return None

    def _sync_parent_children(self, child_id: str, parent_id: str) -> None:
        """Add child_id to parent's children (if not already there)."""
        # Check summary cache first to avoid unnecessary file I/O
        parent_summary = self._summary_cache.get(parent_id)
        if parent_summary and child_id in parent_summary.children:
            return

        try:
            parent = self.read_element(parent_id)
        except KeyError:
            return  # parent not yet created — ok

        if child_id not in parent.children:
            parent.children.append(child_id)
            # Atomic write
            parent_path = self._resolve_path(parent_id)
            parent_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                suffix=".md", prefix="spec-", dir=str(parent_path.parent)
            )
            try:
                write_md_file(Path(tmp_path), parent)
            finally:
                os.close(fd)
            os.replace(tmp_path, parent_path)
            # Update cache
            self._summary_cache[parent_id] = element_to_summary(parent)

    def _remove_from_parent_children(self, child_id: str, parent_id: str) -> None:
        """Remove child_id from parent's children."""
        # Check summary cache first to avoid unnecessary file I/O
        parent_summary = self._summary_cache.get(parent_id)
        if parent_summary and child_id not in parent_summary.children:
            return

        try:
            parent = self.read_element(parent_id)
        except KeyError:
            return

        if child_id in parent.children:
            parent.children.remove(child_id)
            parent_path = self._resolve_path(parent_id)
            parent_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                suffix=".md", prefix="spec-", dir=str(parent_path.parent)
            )
            try:
                write_md_file(Path(tmp_path), parent)
            finally:
                os.close(fd)
            os.replace(tmp_path, parent_path)
            # Update cache
            self._summary_cache[parent_id] = element_to_summary(parent)

    def _cleanup_dangling_references(self, deleted_id: str) -> int:
        """Remove all references to *deleted_id* from other elements.

        Scans the summary cache (already loaded) for:
        - derived_from entries pointing to deleted_id
        - relationship targets pointing to deleted_id
        - children lists containing deleted_id

        Returns the number of elements that were updated.
        """
        updated_count = 0

        # Scan all cached elements for references to the deleted element
        for eid, summary in list(self._summary_cache.items()):
            needs_update = False
            element = None  # lazy load

            # Check derived_from
            if summary.derived_from and deleted_id in summary.derived_from:
                if element is None:
                    try:
                        element = self.read_element(eid)
                    except KeyError:
                        continue
                element.derived_from = [
                    d for d in element.derived_from if d != deleted_id
                ]
                needs_update = True

            # Check relationships (all types)
            if summary.relationships:
                for rel_type, entries in list(summary.relationships.items()):
                    new_entries = [
                        e for e in entries if e.target != deleted_id
                    ]
                    if len(new_entries) != len(entries):
                        if element is None:
                            try:
                                element = self.read_element(eid)
                            except KeyError:
                                continue
                        element.relationships[rel_type] = new_entries
                        needs_update = True

            # Check children (parent → child)
            if summary.children and deleted_id in summary.children:
                if element is None:
                    try:
                        element = self.read_element(eid)
                    except KeyError:
                        continue
                element.children = [
                    c for c in element.children if c != deleted_id
                ]
                needs_update = True

            if needs_update and element is not None:
                parent_path = self._resolve_path(eid)
                parent_path.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp_path = tempfile.mkstemp(
                    suffix=".md", prefix="spec-", dir=str(parent_path.parent)
                )
                try:
                    write_md_file(Path(tmp_path), element)
                finally:
                    os.close(fd)
                os.replace(tmp_path, parent_path)
                # Update caches
                self._summary_cache[eid] = element_to_summary(element)
                self._search_cache[eid] = " ".join(
                    [
                        element.id or "",
                        element.title or "",
                        element.content or "",
                    ]
                ).lower()
                updated_count += 1

        return updated_count

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

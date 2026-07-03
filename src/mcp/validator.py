"""MCP module: format and reference integrity validation."""

from pydantic import BaseModel, Field

from src.config import get_logger
from src.config.methodology import (
    Methodology,
    get_aspect,
    get_element_type,
    get_relationship_type,
)
from src.storage.adapter import StorageAdapter
from src.storage.models import Element
from src.storage.queries import load_all_elements

logger = get_logger(__name__)


class ValidationError(BaseModel):
    element_id: str | None = None
    field: str | None = None
    message: str = ""
    severity: str = "error"


class ValidationReport(BaseModel):
    passed: bool = False
    errors: list[ValidationError] = Field(default_factory=list)
    warnings: list[ValidationError] = Field(default_factory=list)
    fixed: int = 0  # how many broken links fixed
    total_checks: int = 0

    def add_error(self, eid: str | None, field: str | None, msg: str) -> None:
        self.errors.append(
            ValidationError(element_id=eid, field=field, message=msg, severity="error")
        )

    def add_warning(self, eid: str | None, field: str | None, msg: str) -> None:
        self.warnings.append(
            ValidationError(
                element_id=eid, field=field, message=msg, severity="warning"
            )
        )


def _find_parent_cycle(
    element_id: str, parent_id: str | None, elements: list[Element]
) -> str | None:
    """Check if setting element_id.parent = parent_id would create a cycle.

    Walks upward from parent_id through parents. Returns error message
    if element_id is found in ancestor chain (cycle), else None.
    elements: list of all elements (used as lookup by ID).
    """
    if not parent_id:
        return None
    if parent_id == element_id:
        return f"Cannot set parent to self: '{element_id}' → '{parent_id}'"

    # Build ID→Element lookup
    lookup: dict[str, Element] = {e.id: e for e in elements}

    visited = {element_id}
    current = parent_id
    while current:
        if current in visited:
            chain = " → ".join(visited | {current})
            return f"Parent cycle detected: {chain}"
        visited.add(current)
        parent_el = lookup.get(current)
        if parent_el is None:
            break  # parent not in elements — no cycle possible
        current = parent_el.parent
    return None


def validate(
    storage: StorageAdapter, methodology: Methodology, fix: bool = True
) -> ValidationReport:
    """Validate the specification. When fix=True, automatically fixes broken links."""

    report = ValidationReport()
    all_ids: set[str] = set()
    duplicate_ids: set[str] = set()

    all_elements = load_all_elements(storage)
    for element in all_elements:
        if element.id in all_ids:
            duplicate_ids.add(element.id)
        else:
            all_ids.add(element.id)

    for dup_id in duplicate_ids:
        report.add_error(dup_id, "id", "Duplicate ID")

    # Cycle detection: check parent chains
    for element in all_elements:
        if element.id in duplicate_ids:
            continue
        cycle_msg = _find_parent_cycle(element.id, element.parent, all_elements)
        if cycle_msg:
            report.add_error(element.id, "parent", cycle_msg)

    for element in all_elements:
        if element.id in duplicate_ids:
            continue

        # Required fields
        if not element.aspect:
            report.add_error(element.id, "aspect", "Empty aspect name")
        if not element.element_type:
            report.add_error(element.id, "element_type", "Empty element type")
        if not element.title:
            report.add_error(element.id, "title", "Empty title")

        changed = False

        # parent
        if element.parent and element.parent not in all_ids:
            if fix:
                element.parent = None
                changed = True
                report.warnings.append(
                    ValidationError(
                        element_id=element.id,
                        field="parent",
                        severity="warning",
                        message=f"Broken parent reference fixed: removed dangling parent '{element.parent}'",
                    )
                )
                report.fixed += 1
            else:
                report.add_warning(
                    element.id,
                    "parent",
                    f"Broken parent reference: '{element.parent}' does not exist",
                )

        # children
        old_children = list(element.children)
        element.children = [c for c in element.children if c in all_ids]
        if len(element.children) != len(old_children):
            removed = set(old_children) - set(element.children)
            if fix:
                changed = True
                report.warnings.append(
                    ValidationError(
                        element_id=element.id,
                        field="children",
                        severity="warning",
                        message=f"Fixed broken children references: removed {removed} dangling child ID(s)",
                    )
                )
                report.fixed += len(removed)
            else:
                for c in removed:
                    report.add_warning(
                        element.id,
                        "children",
                        f"Broken children reference: '{c}' does not exist",
                    )

        # relationships
        for rel_type, entries in list(element.relationships.items()):
            old_len = len(entries)
            element.relationships[rel_type] = [
                e for e in entries if e.target in all_ids
            ]
            removed_count = old_len - len(element.relationships[rel_type])
            if not element.relationships[rel_type]:
                del element.relationships[rel_type]
            if removed_count > 0:
                if fix:
                    changed = True
                    report.warnings.append(
                        ValidationError(
                            element_id=element.id,
                            field=f"relationships.{rel_type}",
                            severity="warning",
                            message=f"Fixed broken relationship references: removed {removed_count} dangling target(s)",
                        )
                    )
                    report.fixed += removed_count
                else:
                    report.add_warning(
                        element.id,
                        f"relationships.{rel_type}",
                        f"{removed_count} broken relationship reference(s)",
                    )

        # Save the fixed element
        if changed:
            try:
                storage.write_element(element)
            except Exception as exc:
                report.add_error(
                    element.id, None, f"Failed to save fixed element: {exc}"
                )

        # Element and relationship types (do not auto-fix)
        aspect_def = get_aspect(methodology, element.aspect)
        if aspect_def is None:
            report.add_error(
                element.id,
                "aspect",
                f"Element type '{element.element_type}' is unknown for aspect '{element.aspect}'",
            )
        else:
            et_def = get_element_type(methodology, element.aspect, element.element_type)
            if et_def is None:
                valid = [et.name for et in aspect_def.element_types]
                report.add_error(
                    element.id,
                    "element_type",
                    f"Unknown element type '{element.element_type}' for aspect '{element.aspect}'. Valid types: {', '.join(valid)}",
                )
            valid_rels = {rt.name for rt in aspect_def.relationship_types}
            for rel_type in element.relationships:
                # Skip provenance/traceability pseudo-relationships
                if rel_type in ("derived_from",):
                    continue

                if rel_type not in valid_rels:
                    global_rel = get_relationship_type(methodology, rel_type)
                    if global_rel is None:
                        report.add_error(
                            element.id,
                            f"relationships.{rel_type}",
                            f"Relationship type '{rel_type}' is unknown for aspect",
                        )

    report.passed = len(report.errors) == 0
    report.total_checks = len(report.errors) + len(report.warnings)
    return report

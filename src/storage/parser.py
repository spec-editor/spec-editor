"""Parser for .md files with YAML frontmatter for requirements elements."""

from pathlib import Path

import frontmatter
import yaml

from src.storage.models import (
    Element,
    ElementStatus,
    Provenance,
    RelationshipEntry,
)

# Fields stored in frontmatter (all except content)
_FRONTMATTER_FIELDS: tuple[str, ...] = (
    "aspect",
    "element_type",
    "id",
    "title",
    "status",
    "parent",
    "children",
    "relationships",
    "tags",
    "provenance",
    "derived_from",
    "covered_by",
)


def parse_md_file(path: Path) -> Element:
    """Read a .md file and return an Element.

    Raises:
        FileNotFoundError: file not found
        ValueError: invalid YAML frontmatter or missing required fields
    """
    if not path.exists():
        raise FileNotFoundError(f"Operation completed successfully: {path}")

    with open(path, encoding="utf-8") as f:
        post = frontmatter.load(f)

    fm = dict(post.metadata) if post.metadata else {}
    content = post.content or ""

    return frontmatter_to_element(fm, content)


def write_md_file(path: Path, element: Element) -> None:
    """Write an Element to a .md file with YAML frontmatter.

    Creates parent directories if needed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    fm_dict = element_to_frontmatter(element)
    post = frontmatter.Post(content=element.content, **fm_dict)

    with open(path, "w", encoding="utf-8") as f:
        f.write(frontmatter.dumps(post))


def element_to_frontmatter(element: Element) -> dict:
    """Convert an Element to a dict for YAML frontmatter."""
    data = {}

    data["aspect"] = element.aspect
    data["element_type"] = element.element_type
    data["id"] = element.id
    data["title"] = element.title
    data["status"] = element.status.value

    if element.parent is not None:
        data["parent"] = element.parent

    if element.children:
        data["children"] = element.children

    if element.relationships:
        # Serialize RelationshipEntry as dict
        data["relationships"] = {
            rel_type: [entry.model_dump() for entry in entries]
            for rel_type, entries in element.relationships.items()
        }

    if element.tags:
        data["tags"] = element.tags

    if element.provenance is not None:
        data["provenance"] = element.provenance.model_dump()

    if element.derived_from:
        data["derived_from"] = element.derived_from

    if element.covered_by:
        data["covered_by"] = element.covered_by

    return data


def frontmatter_to_element(fm: dict, content: str) -> Element:
    """Create an Element from a frontmatter dict and markdown body."""

    # Parse relationships — supports both formats:
    # 1. Dict: relationships: {relates_to: [{target: "ID"}]}
    # 2. List: relates_to: [MOD-001, SCN-002]  (user-friendly shorthand)
    relationships_raw = fm.get("relationships", {})
    relationships: dict[str, list[RelationshipEntry]] = {}

    # Normalize list-style relationships into dict format
    for key in ("relates_to", "implements", "derived_from", "covered_by", "decided_by", "depends_on"):
        if key in fm:
            value = fm[key]
            if isinstance(value, list):
                relationships_raw[key] = [{"role": key, "target": v} for v in value]
            elif isinstance(value, str):
                relationships_raw[key] = [{"role": key, "target": value}]

    if relationships_raw:
        for rel_type, entries in relationships_raw.items():
            if isinstance(entries, list):
                relationships[rel_type] = [
                    RelationshipEntry(**entry) if isinstance(entry, dict) else entry
                    for entry in entries
                ]

    # Parse provenance
    provenance_raw = fm.get("provenance")
    provenance = Provenance(**provenance_raw) if provenance_raw else None

    return Element(
        aspect=fm.get("aspect", ""),
        element_type=fm.get("element_type", ""),
        id=fm.get("id", ""),
        title=fm.get("title", ""),
        status=ElementStatus(fm.get("status", "draft")),
        parent=fm.get("parent"),
        children=fm.get("children", []),
        relationships=relationships,
        tags=fm.get("tags", []),
        provenance=provenance,
        derived_from=fm.get("derived_from", []),
        covered_by=fm.get("covered_by", []),
        content=content.strip(),
    )

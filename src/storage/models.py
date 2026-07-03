"""Pydantic data models: elements, relationships, aspects."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ElementStatus(str, Enum):
    """Requirements element lifecycle — aligned with STATUSES.md.

    draft ──→ reviewed ──→ confirmed
      │          │
      │          └──→ blocked ──→ draft (PM refine)
      │
      └──→ deprecated (no longer needed / auto-resolved)
    """

    DRAFT = "draft"           # Requires analysis/refinement (analyst-manager)
    REVIEWED = "reviewed"     # Ready for implementation (coding agent)
    CONFIRMED = "confirmed"   # Implemented, tests passed (final state)
    BLOCKED = "blocked"       # Cannot fix, needs PM/analyst refinement
    DEPRECATED = "deprecated" # No longer relevant / auto-closed
    # Legacy — no longer created, only read for backward compat:
    FIXED = "fixed"           # @deprecated — use CONFIRMED
    IMPLEMENTED = "implemented"  # @deprecated — use CONFIRMED


class RelationshipEntry(BaseModel):
    """Element relationship record: role + target element ID."""

    role: str = Field(description="Role in the relationship (parent, child, or custom)")
    target: str = Field(description="Target element ID")


class Provenance(BaseModel):
    """Requirement provenance source."""

    source: str = Field(
        description="Where the requirement came from (document, section, ...)"
    )
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Confidence in the requirement (0..1)"
    )


class Element(BaseModel):
    """Full requirements element model.

    Corresponds to a single .md file with YAML frontmatter.
    """

    aspect: str = Field(description="Name of the aspect to which the element belongs")
    element_type: str = Field(description="Element type according to the methodology")
    id: str = Field(description="Unique identifier within the project")
    title: str = Field(description="Human-readable element name")
    status: ElementStatus = Field(
        default=ElementStatus.DRAFT, description="Lifecycle status"
    )
    parent: str | None = Field(
        default=None, description="Parent element ID (decomposition)"
    )
    children: list[str] = Field(
        default_factory=list, description="Child element IDs (decomposition)"
    )
    relationships: dict[str, list[RelationshipEntry]] = Field(
        default_factory=dict,
        description="Typed relationships: {relationship_type: [RelationshipEntry, ...]}",
    )
    tags: list[str] = Field(
        default_factory=list, description="Tags for search and categorization"
    )
    provenance: Provenance | None = Field(
        default=None, description="Source of the requirement's origin"
    )
    derived_from: list[str] = Field(
        default_factory=list,
        description="IDs of elements from which this one is derived",
    )
    covered_by: list[str] = Field(
        default_factory=list, description="IDs of elements that cover this one"
    )
    implementation_architect: dict[str, Any] | None = Field(
        default=None,
        description="Implementation decisions made by the Implementation Architect agent. "
        "Keys: structure, domain_style, ddd_type, template, layer, ports, adapters. "
        "Other agents SHOULD NOT read or modify this field.",
    )
    content: str = Field(
        default="",
        description="Markdown body of the element (without YAML frontmatter)",
    )


class ElementSummary(BaseModel):
    """Lightweight version of an element for lists (without content)."""

    aspect: str
    element_type: str
    id: str
    title: str
    status: ElementStatus = ElementStatus.DRAFT
    parent: str | None = None
    children: list[str] = Field(default_factory=list)
    relationships: dict[str, list[RelationshipEntry]] = Field(default_factory=dict)
    derived_from: list[str] = Field(default_factory=list)
    implementation_architect: dict[str, Any] | None = Field(default=None)
    tags: list[str] = Field(default_factory=list)


def element_to_summary(element: Element) -> ElementSummary:
    """Create an ElementSummary from a full Element."""
    return ElementSummary(
        aspect=element.aspect,
        element_type=element.element_type,
        id=element.id,
        title=element.title,
        status=element.status,
        parent=element.parent,
        children=element.children or [],
        relationships=element.relationships or {},
        derived_from=element.derived_from or [],
        tags=element.tags,
    )

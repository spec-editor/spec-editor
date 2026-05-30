"""Agent tools (function calling): read, write, validation, metrics."""

from pathlib import Path
from typing import Any, Callable

from src.config.methodology import Methodology
from src.mcp.metrics import MetricsReport, compute_metrics
from src.mcp.validator import ValidationReport, validate
from src.providers.base import ToolDef
from src.storage.adapter import StorageAdapter
from src.storage.models import (
    Element,
    ElementStatus,
    Provenance,
    RelationshipEntry,
)

# ======================================================================
# Read-only tools
# ======================================================================


async def read_element(storage: StorageAdapter, element_id: str) -> dict:
    """Read full contents of a specification element by ID."""
    try:
        element = storage.read_element(element_id)
        return element.model_dump()
    except KeyError:
        return {"error": f"Element with ID '{element_id}' not found"}


async def list_aspect(storage: StorageAdapter, aspect_name: str) -> dict:
    """Get a list of all elements of a given aspect (short form)."""
    elements = storage.list_aspect(aspect_name)
    return {
        "aspect": aspect_name,
        "count": len(elements),
        "elements": [e.model_dump() for e in elements],
    }


async def list_all_elements(storage: StorageAdapter) -> dict:
    """Get a list of all project elements (short form)."""
    elements = storage.list_all()
    return {"total": len(elements), "elements": [e.model_dump() for e in elements]}


async def search_elements(storage: StorageAdapter, query: str) -> dict:
    """Full-text search across elements by ID, title, and content."""
    results = storage.search(query)
    return {
        "query": query,
        "found": len(results),
        "elements": [r.model_dump() for r in results],
    }


async def find_related(storage: StorageAdapter, element_id: str) -> dict:
    """Find all elements related to the specified one."""
    related = storage.find_related(element_id)
    return {
        "element_id": element_id,
        "related_count": len(related),
        "related": [r.model_dump() for r in related],
    }


async def get_methodology_tool(methodology: Methodology) -> dict:
    """Get the description of the current methodology."""
    return {
        "name": methodology.name,
        "version": methodology.version,
        "description": methodology.description,
        "aspects": [
            {
                "name": a.name,
                "title": a.title,
                "element_types": [
                    {"name": et.name, "title": et.title} for et in a.element_types
                ],
                "relationship_types": [
                    {
                        "name": rt.name,
                        "title": rt.title,
                        "source_aspects": rt.source_aspects,
                        "target_aspects": rt.target_aspects,
                        "cardinality": rt.cardinality,
                    }
                    for rt in a.relationship_types
                ],
            }
            for a in methodology.aspects
        ],
    }


async def run_validate_tool(storage: StorageAdapter, methodology: Methodology) -> dict:
    """Run MCP validation."""
    report: ValidationReport = validate(storage, methodology, fix=True)
    return report.model_dump()


async def run_metrics_tool(storage: StorageAdapter) -> dict:
    """Compute specification connectivity metrics."""
    report: MetricsReport = compute_metrics(storage)
    return report.model_dump()


# ======================================================================
# Write tools (Agent 1 and Agent 2 only)
# ======================================================================


async def write_element_tool(
    storage: StorageAdapter,
    aspect: str,
    element_type: str,
    id: str,
    title: str,
    content: str = "",
    parent: str | None = None,
    children: list[str] | None = None,
    relationships: dict | None = None,
    tags: list[str] | None = None,
    status: str = "draft",
    provenance_source: str | None = None,
    derived_from: list[str] | None = None,
) -> dict:
    """Create a new or update an existing specification element."""
    try:
        rel_entries: dict[str, list[RelationshipEntry]] = {}
        if relationships:
            for rel_type, entries in relationships.items():
                if isinstance(entries, list):
                    rel_entries[rel_type] = [
                        RelationshipEntry(**e) if isinstance(e, dict) else e
                        for e in entries
                    ]

        prov = None
        if provenance_source:
            prov = Provenance(source=provenance_source)

        element = Element(
            aspect=aspect,
            element_type=element_type,
            id=id,
            title=title,
            content=content,
            parent=parent,
            children=children or [],
            relationships=rel_entries,
            tags=tags or [],
            status=ElementStatus(status),
            provenance=prov,
            derived_from=derived_from or [],
        )
        storage.write_element(element)
        return {"status": "ok", "element_id": id, "message": f"Element '{id}' written"}
    except Exception as exc:
        return {"status": "error", "element_id": id, "message": str(exc)}


async def delete_element_tool(storage: StorageAdapter, element_id: str) -> dict:
    """Delete a specification element by ID.
    SRC elements (sources) must NOT be deleted.
    Before deletion, saves a copy to aspects/_deleted/ for audit.
    """
    if element_id.startswith("SRC-"):
        return {
            "status": "error",
            "element_id": element_id,
            "message": "SRC elements cannot be deleted — they are requirement sources.",
        }
    try:
        # Save a copy for audit
        try:
            element = storage.read_element(element_id)
            if hasattr(storage, "_aspects_path"):
                archive_dir = storage._aspects_path / "_deleted"
                archive_dir.mkdir(parents=True, exist_ok=True)
                import json
                import time

                ts = int(time.time())
                archive = archive_dir / f"{element_id}_{ts}.json"
                archive.write_text(element.model_dump_json(indent=2))
        except Exception:
            pass  # archive — best effort

        storage.delete_element(element_id)
        return {
            "status": "ok",
            "element_id": element_id,
            "message": f"Element '{element_id}' deleted",
        }
    except KeyError:
        return {
            "status": "error",
            "element_id": element_id,
            "message": "Element not found",
        }


async def add_relationship_tool(
    storage: StorageAdapter,
    source_id: str,
    rel_type: str,
    target_id: str,
    role: str = "relates_to",
) -> dict:
    """Add a relationship between two elements."""
    try:
        element = storage.read_element(source_id)
        entry = RelationshipEntry(role=role, target=target_id)
        if rel_type not in element.relationships:
            element.relationships[rel_type] = []
        if any(e.target == target_id for e in element.relationships[rel_type]):
            return {"status": "ok", "message": "Relationship already exists"}
        element.relationships[rel_type].append(entry)
        storage.write_element(element)
        return {
            "status": "ok",
            "message": f"Relationship {rel_type} -> {target_id} added",
        }
    except KeyError:
        return {"status": "error", "message": f"Element '{source_id}' not found"}


async def remove_relationship_tool(
    storage: StorageAdapter,
    source_id: str,
    rel_type: str,
    target_id: str,
) -> dict:
    """Remove a relationship between elements."""
    try:
        element = storage.read_element(source_id)
        if rel_type in element.relationships:
            element.relationships[rel_type] = [
                e for e in element.relationships[rel_type] if e.target != target_id
            ]
            if not element.relationships[rel_type]:
                del element.relationships[rel_type]
            storage.write_element(element)
        return {
            "status": "ok",
            "message": f"Relationship {rel_type} -> {target_id} removed",
        }
    except KeyError:
        return {"status": "error", "message": f"Element '{source_id}' not found"}


async def report_complete(storage: StorageAdapter | None = None) -> dict:
    """Report that the agent considers the requirements complete.
    Checks metrics: rejects if connectivity < 1.0 or there are orphans."""
    if storage:
        from src.mcp.metrics import compute_metrics

        m = compute_metrics(storage)
        issues = []
        if m.connectivity_index < 0.7:
            issues.append(
                f"connectivity_index={m.connectivity_index:.2f} (need >= 0.7)"
            )
        if m.orphan_elements > 0:
            issues.append(f"orphan_elements={m.orphan_elements} (need 0)")
        if issues:
            return {
                "status": "error",
                "declaration": "rejected",
                "message": f"Cannot complete: {', '.join(issues)}. Continue working.",
            }
    return {"status": "ok", "declaration": "complete"}


async def escalate(reason: str) -> dict:
    """Escalate a conflict to the orchestrator."""
    return {"status": "ok", "declaration": "conflict", "reason": reason}


async def request_clarification(question: str) -> dict:
    """Request clarification from the customer."""
    return {
        "status": "ok",
        "declaration": "clarification_request",
        "question": question,
    }


# ======================================================================
# ToolDef — JSON Schema for function calling
# ======================================================================


def _params(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


RO_TOOLS: list[ToolDef] = [
    ToolDef(
        name="read_element",
        description="Read an element by ID. Returns aspect, element_type, title, status, parent, children, relationships, content.",
        parameters=_params(
            {"element_id": {"type": "string", "description": "Element ID"}},
            ["element_id"],
        ),
    ),
    ToolDef(
        name="list_aspect",
        description="List all elements of an aspect.",
        parameters=_params(
            {"aspect_name": {"type": "string", "description": "Aspect name"}},
            ["aspect_name"],
        ),
    ),
    ToolDef(
        name="list_all_elements",
        description="List all project elements.",
        parameters=_params({}),
    ),
    ToolDef(
        name="search_elements",
        description="Full-text search by ID, title, and content.",
        parameters=_params(
            {"query": {"type": "string", "description": "Search query"}}, ["query"]
        ),
    ),
    ToolDef(
        name="find_related",
        description="Find elements related to the specified one.",
        parameters=_params(
            {"element_id": {"type": "string", "description": "Element ID"}},
            ["element_id"],
        ),
    ),
    ToolDef(
        name="get_methodology",
        description="Get the methodology description.",
        parameters=_params({}),
    ),
    ToolDef(
        name="run_validate",
        description="Run MCP specification validation.",
        parameters=_params({}),
    ),
    ToolDef(
        name="run_metrics",
        description="Compute connectivity metrics.",
        parameters=_params({}),
    ),
]

from src.agents.tools_code import CODE_RO_TOOLS, add_code_tools_handlers  # noqa: E402
from src.agents.tools_questions import (
    QUESTIONS_RO_TOOLS,
    QUESTIONS_RW_TOOLS,
    add_question_tools_handlers,
)

RO_TOOLS.extend(CODE_RO_TOOLS)
RO_TOOLS.extend(QUESTIONS_RO_TOOLS)

RW_TOOLS: list[ToolDef] = [
    ToolDef(
        name="write_element",
        description="Create or update an element.",
        parameters=_params(
            {
                "aspect": {"type": "string", "description": "Aspect name"},
                "element_type": {"type": "string", "description": "Element type"},
                "id": {"type": "string", "description": "Unique ID"},
                "title": {"type": "string", "description": "Element title"},
                "content": {"type": "string", "description": "Markdown description"},
                "parent": {
                    "type": "string",
                    "description": "Parent ID (optional)",
                },
                "children": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Child element IDs",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags",
                },
                "status": {
                    "type": "string",
                    "description": "draft, reviewed, confirmed, deprecated",
                },
                "provenance_source": {
                    "type": "string",
                    "description": "Source file of the requirement (for traceability)",
                },
                "derived_from": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "IDs of source requirements (SRC-*) from which the element is derived",
                },
            },
            ["aspect", "element_type", "id", "title", "derived_from"],
        ),
    ),
    ToolDef(
        name="delete_element",
        description="Delete an element by ID.",
        parameters=_params(
            {"element_id": {"type": "string", "description": "Element ID"}},
            ["element_id"],
        ),
    ),
    ToolDef(
        name="add_relationship",
        description="Add a relationship between elements.",
        parameters=_params(
            {
                "source_id": {"type": "string", "description": "Source element ID"},
                "rel_type": {"type": "string", "description": "Relationship type"},
                "target_id": {"type": "string", "description": "Target element ID"},
                "role": {"type": "string", "description": "Role in the relationship"},
            },
            ["source_id", "rel_type", "target_id"],
        ),
    ),
    ToolDef(
        name="remove_relationship",
        description="Remove a relationship.",
        parameters=_params(
            {
                "source_id": {"type": "string"},
                "rel_type": {"type": "string"},
                "target_id": {"type": "string"},
            },
            ["source_id", "rel_type", "target_id"],
        ),
    ),
    ToolDef(
        name="report_complete",
        description="Declare requirements completeness.",
        parameters=_params({}),
    ),
    ToolDef(
        name="escalate",
        description="Escalate a conflict.",
        parameters=_params({"reason": {"type": "string"}}, ["reason"]),
    ),
    ToolDef(
        name="request_clarification",
        description="Request clarification from a person.",
        parameters=_params({"question": {"type": "string"}}, ["question"]),
    ),
]

RW_TOOLS.extend(QUESTIONS_RW_TOOLS)


def get_tool_definitions(writable: bool = False) -> list[ToolDef]:
    """Get ToolDefs. writable=True — all tools, otherwise only read-only."""
    if writable:
        return RO_TOOLS + RW_TOOLS
    return RO_TOOLS


# Handler dicts: tool name -> async callable


def build_read_only_handlers(
    storage: StorageAdapter,
    methodology: Methodology,
    source_dir: str | None = None,
    spawner: Callable | None = None,
    agent_for_compact=None,
    srs_template_path: str = "srs_template.yaml",
) -> dict[str, Callable]:
    """Build a handler dict for read-only tools."""
    sd = source_dir or ""
    handlers = {
        "read_element": lambda element_id: read_element(storage, element_id),
        "list_aspect": lambda aspect_name: list_aspect(storage, aspect_name),
        "list_all_elements": lambda: list_all_elements(storage),
        "search_elements": lambda query: search_elements(storage, query),
        "find_related": lambda element_id: find_related(storage, element_id),
        "get_methodology": lambda: get_methodology_tool(methodology),
        "run_validate": lambda: run_validate_tool(storage, methodology),
        "run_metrics": lambda: run_metrics_tool(storage),
    }
    add_question_tools_handlers(handlers, sd)
    add_code_tools_handlers(
        handlers,
        storage,
        methodology,
        sd,
        spawner,
        agent_for_compact,
        srs_template_path,
    )
    return handlers


def build_all_handlers(
    storage: StorageAdapter,
    methodology: Methodology,
    source_dir: str | None = None,
    spawner: Callable | None = None,
    agent_for_compact=None,
    srs_template_path: str = "srs_template.yaml",
) -> dict[str, Callable]:
    """Build a handler dict for all tools (read + write)."""
    _sd = source_dir or ""

    handlers = build_read_only_handlers(
        storage, methodology, source_dir, spawner, agent_for_compact, srs_template_path
    )
    handlers.update(
        {
            "write_element": lambda **kw: write_element_tool(storage, **kw),
            "delete_element": lambda element_id: delete_element_tool(
                storage, element_id
            ),
            "add_relationship": lambda **kw: add_relationship_tool(storage, **kw),
            "remove_relationship": lambda **kw: remove_relationship_tool(storage, **kw),
            "report_complete": lambda: report_complete(storage),
            "escalate": lambda reason: escalate(reason),
            "request_clarification": lambda question: request_clarification(question),
        }
    )
    add_question_tools_handlers(handlers, _sd)
    return handlers

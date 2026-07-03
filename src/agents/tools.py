"""Agent tools (function calling): read, write, validation, metrics."""

import contextvars
from pathlib import Path
from typing import Any, Callable

from src.config.methodology import Methodology, get_aspect
from src.context.builder import ContextBuilder
from src.mcp.metrics import MetricsReport, compute_metrics
from src.mcp.validator import ValidationReport, validate
from src.providers.base import ToolDef
from src.providers.base import make_tool_params as _params
from src.storage.adapter import StorageAdapter
from src.storage.models import (
    Element,
    ElementStatus,
    Provenance,
    RelationshipEntry,
)

from src.agents.constants import DEFAULT_REASONING_MODEL

# ── Auth context ────────────────────────────────────────────────────

# Set by MCP server or other callers to identify the current user.
# Agents set this to their agent ID (e.g. "agent_1").
_tool_caller: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_tool_caller", default=""
)
# Project path context — set by MCP server for auth resolution.
_tool_project_path: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_tool_project_path", default=""
)


def set_tool_caller(caller: str, project_path: str = "") -> None:
    """Set the current tool caller identity for auth checks."""
    _tool_caller.set(caller)
    if project_path:
        _tool_project_path.set(project_path)


def _check_auth(storage: Any, resource: str, action: str) -> bool:
    """Check if the current caller can perform an action on a resource.

    Uses AuthProvider if configured. Returns True if:
    - No caller is set (backward compat — no auth check)
    - Auth backend is noop (allow all)
    - Permission is explicitly granted

    Args:
        storage: StorageAdapter (for finding project path)
        resource: Element ID or "*" for global actions
        action: "read", "write", "delete", "admin"
    """
    caller = _tool_caller.get("")
    if not caller:
        return True  # No caller set — allow (backward compat)

    project_path = _tool_project_path.get("")
    if not project_path:
        # Try to extract from storage
        if hasattr(storage, "_aspects_path"):
            pp = Path(storage._aspects_path).parent
            project_path = str(pp)

    if not project_path:
        return True  # Can't determine project — allow

    try:
        from src.auth import create_auth_provider
        auth = create_auth_provider(project_path)
        return auth.check(caller, resource, action)
    except Exception:
        return True  # Auth failure — allow (fail-open for backward compat)


async def set_log_config_tool(
    level: str = "",
    modules: list[str] | None = None,
    elements: list[str] | None = None,
    silenced: list[str] | None = None,
) -> dict:
    """Update runtime logging configuration in local.yaml.

    Allows agents (PM, coding, QA) to control which modules/elements
    are logged during a debug cycle. Changes take effect within 5 seconds
    (TTL cache expiry of the logging backend).

    Args:
        level: Minimum severity (debug, info, warning, error). Empty = no change.
        modules: Only log these MOD-* IDs. Empty list = log all. None = no change.
        elements: Only log events for these element IDs. Empty list = log all. None = no change.
        silenced: Never log these MOD-* IDs. Empty list = none silenced. None = no change.
    """
    project_path = _tool_project_path.get("")
    if not project_path:
        return {"status": "error", "message": "No project context — set_log_config requires MCP caller context"}

    import yaml
    from pathlib import Path

    yaml_path = Path(project_path) / "local.yaml"
    if not yaml_path.exists():
        return {"status": "error", "message": f"local.yaml not found at {project_path}"}

    try:
        data = yaml.safe_load(yaml_path.read_text()) or {}
    except Exception:
        data = {}

    logging_cfg = data.get("logging", {})
    changed = []

    if level:
        if level not in ("debug", "info", "warning", "error"):
            return {"status": "error", "message": f"Invalid level: {level}. Use debug, info, warning, or error."}
        logging_cfg["level"] = level
        changed.append(f"level={level}")

    if modules is not None:
        logging_cfg["modules"] = list(modules)
        changed.append(f"modules={modules}")

    if elements is not None:
        logging_cfg["elements"] = list(elements)
        changed.append(f"elements={len(elements)} element(s)")

    if silenced is not None:
        logging_cfg["silenced"] = list(silenced)
        changed.append(f"silenced={len(silenced)} module(s)")

    data["logging"] = logging_cfg

    # Write back atomically
    import tempfile, os
    fd, tmp = tempfile.mkstemp(dir=str(yaml_path.parent), suffix=".yaml")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
        os.replace(tmp, str(yaml_path))
    except Exception:
        os.unlink(tmp)
        raise

    return {
        "status": "ok",
        "message": f"Logging config updated: {', '.join(changed)}",
        "current": logging_cfg,
    }


# ======================================================================
# Read-only tools
# ======================================================================


async def read_element(
    storage: StorageAdapter, element_id: str, deep: bool = False
) -> dict:
    """Read full contents of a specification element by ID.

    If deep=True, also returns the element's children, parent, and
    relationship targets — providing full context in one call.
    """
    try:
        element = storage.read_element(element_id)
        result = element.model_dump()

        if deep:
            # Expand children
            if element.children:
                children_data = []
                for child_id in element.children:
                    try:
                        child = storage.read_element(child_id)
                        children_data.append(
                            child.model_dump(exclude={"children", "relationships"})
                        )
                    except KeyError:
                        children_data.append({"id": child_id, "error": "not found"})
                result["_children"] = children_data

            # Expand relationship targets (one level deep)
            if element.relationships:
                related_data: dict[str, list[dict]] = {}
                for rel_type, entries in element.relationships.items():
                    targets = []
                    for entry in entries:
                        try:
                            target = storage.read_element(entry.target)
                            targets.append(
                                {
                                    "id": target.id,
                                    "title": target.title,
                                    "aspect": target.aspect,
                                    "element_type": target.element_type,
                                }
                            )
                        except KeyError:
                            targets.append({"id": entry.target, "error": "not found"})
                    related_data[rel_type] = targets
                result["_related"] = related_data

            # Expand parent
            if element.parent:
                try:
                    parent = storage.read_element(element.parent)
                    result["_parent"] = {
                        "id": parent.id,
                        "title": parent.title,
                        "aspect": parent.aspect,
                    }
                except KeyError:
                    result["_parent"] = {"id": element.parent, "error": "not found"}

        return result
    except KeyError:
        return {"error": f"Element with ID '{element_id}' not found"}


async def list_aspect(
    storage: StorageAdapter, aspect_name: str, offset: int = 0, limit: int = 0
) -> dict:
    """Get a list of all elements of a given aspect (short form).

    Args:
        aspect_name: Aspect name
        offset: Number of results to skip (0 = start)
        limit: Max results to return (0 = all)
    """
    elements = storage.list_aspect(aspect_name, offset=offset, limit=limit)
    total = storage.count_aspect(aspect_name)
    return {
        "aspect": aspect_name,
        "total": total,
        "count": len(elements),
        "elements": [e.model_dump() for e in elements],
    }


async def list_all_elements(
    storage: StorageAdapter, offset: int = 0, limit: int = 0
) -> dict:
    """Get a list of all project elements (short form).

    Args:
        offset: Number of results to skip (0 = start)
        limit: Max results to return (0 = all)
    """
    # Rebuild index to pick up disk changes from external tools (reengineer CLI etc.)
    storage._rebuild_index()
    elements = storage.list_all(offset=offset, limit=limit)
    total = storage.count_all()
    return {"total": total, "elements": [e.model_dump() for e in elements]}


async def search_elements(
    storage: StorageAdapter, query: str, offset: int = 0, limit: int = 0
) -> dict:
    """Full-text search across elements by ID, title, and content.

    Args:
        query: Search string
        offset: Number of results to skip (0 = start)
        limit: Max results to return (0 = all)
    """
    all_results = storage.search(query)
    found = len(all_results)
    if offset or limit:
        if offset:
            sliced = all_results[offset:]
        else:
            sliced = all_results
        if limit:
            sliced = sliced[:limit]
        elements = [r.model_dump() for r in sliced]
    else:
        elements = [r.model_dump() for r in all_results]
    return {
        "query": query,
        "found": found,
        "elements": elements,
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
                "default_diagram": a.default_diagram,
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


async def get_context_for_file_tool(storage, file_path: str) -> dict:
    """Build spec context for a code file (MCP tool handler)."""
    builder = ContextBuilder(storage)
    ctx = builder.for_file(Path(file_path))
    return {"context": ctx}


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


async def _check_parent_cycle(
    storage: StorageAdapter, new_element_id: str, proposed_parent_id: str
) -> bool:
    """Check if setting parent=proposed_parent_id for new_element_id creates a cycle.

    Walks up the parent chain from proposed_parent_id and returns True
    if new_element_id appears in the ancestor chain (i.e., cycle detected).

    This is O(depth) — walks at most the depth of the tree.
    """
    visited: set[str] = set()
    current_id: str | None = proposed_parent_id

    while current_id and current_id not in visited:
        if current_id == new_element_id:
            return True  # CYCLE: new element would be its own ancestor
        visited.add(current_id)
        try:
            current_el = storage.read_element(current_id)
            current_id = current_el.parent or None
        except Exception:
            break  # element not found — stop walking

    return False


async def write_element_tool(
    storage: StorageAdapter,
    methodology: Methodology,
    aspect: str = "",
    element_type: str = "",
    title: str = "",
    id: str = "",
    content: str = "",
    parent: str | None = None,
    children: list[str] | None = None,
    relationships: dict | None = None,
    tags: list[str] | None = None,
    status: str = "draft",
    provenance_source: str | None = None,
    derived_from: list[str] | None = None,
) -> dict:
    """Create a new or update an existing specification element.

    For UPDATES (id already exists): only id is required. Other fields
    are inherited from the existing element if not provided.
    For CREATES (new id): aspect, element_type, title are required.

    Auth: checks write permission via AuthProvider if configured.
    """
    # ── Auth check ──
    if not _check_auth(storage, id or "new", "write"):
        return {"status": "error", "element_id": id, "message": "Access denied: insufficient permissions to write elements"}

    # For updates, inherit missing fields from existing element
    is_update = storage.exists(id) if id else False
    if is_update:
        try:
            existing = storage.read_element(id)
            if not aspect:
                aspect = existing.aspect
            if not element_type:
                element_type = existing.element_type
            if not title:
                title = existing.title
        except Exception:
            pass

    # Auto-generate ID if not provided
    if not id:
        # Build prefix from element_type (e.g. module -> MOD, entity -> ENT)
        words = element_type.replace("_", " ").replace("-", " ").split()
        pfx = "".join(w[0] for w in words).upper()
        if len(pfx) < 2:
            pfx = element_type[:3].upper()
        existing = storage.list_all()
        nums = [
            int(e.id.split("-")[-1])
            for e in existing
            if e.id.startswith(f"{pfx}-") and e.id.split("-")[-1].isdigit()
        ]
        next_num = max(nums) + 1 if nums else 1
        id = f"{pfx}-{next_num:03d}"
    # Validate aspect and element_type against methodology
    aspect_def = get_aspect(methodology, aspect)
    if aspect_def is None:
        valid_aspects = [a.name for a in methodology.aspects]
        return {
            "status": "error",
            "element_id": id,
            "message": f"Unknown aspect '{aspect}'. Valid aspects: {', '.join(valid_aspects)}",
        }
    valid_types = [et.name for et in aspect_def.element_types]
    if element_type not in valid_types:
        return {
            "status": "error",
            "element_id": id,
            "message": f"Unknown element_type '{element_type}' in aspect '{aspect}'. Valid types: {', '.join(valid_types)}",
        }

    # ── Validate parent via methodology hierarchy ──
    from src.config.methodology import get_hierarchy

    hierarchy = get_hierarchy(methodology, aspect)
    if hierarchy:
        expected_parent_type = hierarchy.get(element_type)
        if expected_parent_type and not parent:
            return {
                "status": "error",
                "element_id": id,
                "message": (
                    f"Element type '{element_type}' in aspect '{aspect}' "
                    f"MUST have a parent of type '{expected_parent_type}'. "
                    f"Add parent='ID' to write_element."
                ),
            }
        # Validate parent type (soft warning, not error — agent might reference
        # a parent from a different aspect or a not-yet-created element)
        if parent and expected_parent_type:
            try:
                parent_el = storage.read_element(parent)
                parent_el_type = parent_el.element_type
                if parent_el_type != expected_parent_type:
                    # Special case: same type can be nested (section→section, module→module)
                    if parent_el_type != element_type:
                        return {
                            "status": "error",
                            "element_id": id,
                            "message": (
                                f"Element '{id}' (type '{element_type}') should have "
                                f"parent of type '{expected_parent_type}', but "
                                f"'{parent}' is type '{parent_el_type}'. "
                                f"Hierarchy: {hierarchy}"
                            ),
                        }
            except Exception:
                pass  # parent not found yet — OK, might be created later

    # ── Cycle detection ──
    if parent:
        cycled = await _check_parent_cycle(storage, id, parent)
        if cycled:
            return {
                "status": "error",
                "element_id": id,
                "message": (
                    f"Cycle detected: setting parent='{parent}' for '{id}' "
                    f"would create a loop through ancestors. "
                    f"Choose a different parent or leave empty."
                ),
            }

    try:
        # For updates: inherit existing fields not explicitly provided
        if is_update and id:
            try:
                existing = storage.read_element(id)
                if children is None and existing.children:
                    children = existing.children
                if not relationships and existing.relationships:
                    relationships = {
                        k: [e.model_dump() if hasattr(e, 'model_dump') else e for e in v]
                        for k, v in existing.relationships.items()
                    }
                if tags is None and existing.tags:
                    tags = existing.tags
                if not content and existing.content:
                    content = existing.content
                if derived_from is None and existing.derived_from:
                    derived_from = existing.derived_from
            except Exception:
                pass

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

        # Auto-update parent's children list + add consists_of relationship
        if parent:
            try:
                parent_el = storage.read_element(parent)
                if id not in (parent_el.children or []):
                    parent_el.children = (parent_el.children or []) + [id]
                # Add consists_of relationship if same aspect
                if element.aspect == parent_el.aspect:
                    rels = parent_el.relationships or {}
                    entry = RelationshipEntry(role="consists_of", target=id)
                    existing = rels.get("consists_of", [])
                    if not any(e.target == id for e in existing):
                        rels["consists_of"] = existing + [entry]
                    parent_el.relationships = rels
                storage.write_element(parent_el)
            except Exception:
                pass  # parent may not exist yet

        return {"status": "ok", "element_id": id, "message": f"Element '{id}' written"}
    except Exception as exc:
        return {"status": "error", "element_id": id, "message": str(exc)}


async def delete_element_tool(
    storage: StorageAdapter, element_id: str, force: bool = False
) -> dict:
    """Delete a specification element by ID.

    If SPEC_EDITOR__RESTRICT_SOURCE_DELETION=true (default), SRC-*
    elements are protected from deletion by AI agents. Pass force=True
    to bypass (VSCode UI uses this).

    Auth: checks delete permission via AuthProvider if configured.
    Before deletion, saves a copy to aspects/_deleted/ for audit.
    """
    # ── Auth check ──
    if not _check_auth(storage, element_id, "delete"):
        return {"status": "error", "element_id": element_id, "message": "Access denied: insufficient permissions to delete elements"}

    from src.config import Settings

    if not force:
        try:
            settings = Settings()
            if settings.restrict_source_deletion and element_id.startswith("SRC-"):
                return {
                    "status": "error",
                    "element_id": element_id,
                    "message": (
                        "SRC elements are protected from deletion by agents. "
                        "Set 'restrictSourceDeletion' to false in VSCode settings "
                        "to allow deletion, or use the VSCode UI to delete."
                    ),
                }
        except Exception:
            pass  # Settings not available in test — fall through
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
    methodology: Methodology | None = None,
) -> dict:
    """Add a relationship between two elements."""
    try:
        element = storage.read_element(source_id)

        # Validate relationship type against methodology
        if methodology:
            from src.config.methodology import get_aspect, get_relationship_type

            aspect_def = get_aspect(methodology, element.aspect)
            if aspect_def:
                valid_rels = {rt.name for rt in aspect_def.relationship_types}
                if rel_type not in valid_rels:
                    global_rel = get_relationship_type(methodology, rel_type)
                    if global_rel is None:
                        return {
                            "status": "error",
                            "message": (
                                f"Relationship type '{rel_type}' is unknown for aspect "
                                f"'{element.aspect}' and not global. "
                                f"Valid types: {', '.join(sorted(valid_rels))}"
                            ),
                        }

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
    except FileNotFoundError as exc:
        return {
            "status": "error",
            "message": f"Element '{source_id}' file missing (index stale?): {exc}",
        }


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


async def report_complete(
    storage: StorageAdapter | None = None, ci_threshold: float = 0.7
) -> dict:
    """Report that the agent considers the requirements complete.
    Checks metrics: rejects if connectivity < ci_threshold or there are orphans."""
    if storage:
        from src.mcp.metrics import compute_metrics

        m = compute_metrics(storage)
        issues = []
        if m.connectivity_index < ci_threshold:
            issues.append(
                f"connectivity_index={m.connectivity_index:.2f} (need >= {ci_threshold})"
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


def _resolve_module(storage: StorageAdapter, element_id: str) -> str:
    """Resolve which module (modules aspect) an element belongs to.

    Walks relationships, parent chain, and derived_from to find the
    nearest element in the ``modules`` aspect.  Falls back to the first
    non-``sources`` aspect if no module is found.
    """
    # ── BFS through spec connections ──
    visited: set[str] = {element_id}
    queue: list[str] = [element_id]
    while queue:
        current_id = queue.pop(0)
        try:
            current = storage.read_element(current_id)
        except Exception:
            continue

        # Check relationships for module targets
        for entries in (current.relationships or {}).values():
            for entry in entries:
                if entry.target in visited:
                    continue
                visited.add(entry.target)
                try:
                    target = storage.read_element(entry.target)
                    if target.aspect == "modules":
                        return target.id
                    queue.append(entry.target)
                except Exception:
                    pass

        # Walk parent
        if current.parent and current.parent not in visited:
            visited.add(current.parent)
            queue.append(current.parent)

        # Walk derived_from
        for src_id in (current.derived_from or []):
            if src_id not in visited:
                visited.add(src_id)
                queue.append(src_id)

    # ── Fallback: first non-sources aspect ──
    aspects: set[str] = set()
    for e in storage.list_all():
        if e.aspect and e.aspect != "sources":
            aspects.add(e.aspect)
    return sorted(aspects)[0] if aspects else "?"


async def fix_bugs_parallel_tool(
    storage: StorageAdapter,
    project_path: str = "",
    model: str = DEFAULT_REASONING_MODEL,
    max_parallel: int = 5,
) -> dict:
    """Push all active SRC-BUG-* elements to the coding agent queue.

    Each bug becomes a separate task in Redis for parallel processing
    by multiple coding agent workers.  The workers pick up tasks
    independently - achieving true parallelism across workers.

    Args:
        model: LLM model to use for coding
        max_parallel: Max concurrent tasks to dispatch (Redis cap)
    """
    from pathlib import Path

    from src.agents.task_queue import AbstractTaskQueue
    from src.config import get_logger

    _log = get_logger(__name__)

    elements = storage.list_all()
    bugs = [
        e
        for e in elements
        if getattr(e, "status", "") == "reviewed"
        and "permanent_blocked" not in getattr(e, "tags", [])
        and "dispatched" not in getattr(e, "tags", [])
    ]

    if not bugs:
        return {"status": "ok", "dispatched": 0, "message": "No active bugs"}

    # Resolve queue URL from project config (with project prefix)
    from src.agents.events import get_queue_url

    pp = Path(project_path) if project_path else Path(".")
    queue_url = get_queue_url(pp)

    # ── One task per module: resolve dispatched modules ──
    busy_modules: set[str] = set()
    for e in elements:
        if "dispatched" in getattr(e, "tags", []):
            mod = _resolve_module(storage, e.id) if hasattr(e, "id") else "?"
            busy_modules.add(mod)

    # Push tasks to Redis (respect max_parallel cap, 1 per module)
    queue = AbstractTaskQueue.connect(queue_url)
    await queue.connect()

    dispatched = 0
    errors = 0
    modules: dict[str, int] = {}
    skipped_busy = 0
    for bug in bugs:
        if dispatched >= max_parallel:
            break

        # Skip if module is busy (already has a dispatched or in-progress task)
        mod = _resolve_module(storage, bug.id) if hasattr(bug, "id") else "?"
        if mod in busy_modules:
            skipped_busy += 1
            continue
        busy_modules.add(mod)  # reserve this module for the current batch
        try:
            modules[mod] = modules.get(mod, 0) + 1

            # ElementSummary has no .content — the coding agent reads the full
            # element via read_element when processing, so this is just context.
            content = getattr(bug, "content", None)
            # The requirement this bug targets — used to find test_{target}_*.py
            affected_id = bug.derived_from[0] if (hasattr(bug, "derived_from") and bug.derived_from) else ""
            task_payload = {
                "bug_id": bug.id,
                "leaf_id": affected_id,
                "task": (
                    f"Fix {bug.id} (attempt 1/3): {bug.title}\n\n"
                    f"{content or ''}\n\n"
                    f"PROJECT: {project_path}\n"
                    f"Read FILES TO CHECK mentioned above before making changes.\n"
                    f"Use bash to run: python -m pytest tests/ -q to verify fixes."
                ),
                "model": model,
                "attempt": 1,
            }
            tid = await queue.push("coding", task_payload)
            # Mark as dispatched to prevent re-dispatch on next iteration
            try:
                full = storage.read_element(bug.id)
                if "dispatched" not in (full.tags or []):
                    full.tags = (full.tags or []) + ["dispatched"]
                    storage.write_element(full)
            except Exception:
                pass  # best-effort — coding agent will clear 'dispatched' on start
            _log.info(
                "fix_bugs_dispatched",
                bug_id=bug.id,
                task_id=tid,
            )
            dispatched += 1
        except Exception as exc:
            errors += 1
            _log.error(
                "fix_bugs_dispatch_error",
                bug_id=bug.id,
                error=str(exc),
            )

    await queue.close()
    return {
        "status": "ok",
        "dispatched": dispatched,
        "errors": errors,
        "skipped_busy": skipped_busy,
        "queue": queue_url,
        "remaining": len(bugs) - dispatched - skipped_busy,
        "modules": modules,
    }


async def cleanup_fixed_bugs_tool(storage: StorageAdapter) -> dict:
    """Archive SRC-BUG-* elements with status='deprecated'.

    Instead of permanent deletion, deprecated bugs are MOVED to
    aspects/_deleted/ as JSON snapshots.  This preserves the full
    element data (including decisions, derived_from links, and
    traceability audit trail) while removing the .md file from
    the active aspects/ tree.

    Returns count of archived bugs.
    """
    import json as _json
    from datetime import datetime, timezone
    from pathlib import Path as _Path

    _log = get_logger(__name__)

    elements = storage.list_all()
    bugs = [
        e
        for e in elements
        if e.id.startswith("SRC-BUG-") and getattr(e, "status", "") == "deprecated"
    ]

    # Resolve project root from storage path
    proj_root = _Path(storage._project_path) if hasattr(storage, "_project_path") else _Path.cwd()
    deleted_dir = proj_root / "aspects" / "_deleted"
    deleted_dir.mkdir(parents=True, exist_ok=True)

    archived = 0
    errors = 0
    for bug in bugs:
        try:
            # Read full element before archiving
            full = storage.read_element(bug.id)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

            # Serialize to JSON snapshot
            snapshot = {
                "id": full.id,
                "aspect": full.aspect,
                "element_type": full.element_type,
                "title": full.title,
                "status": full.status.value if hasattr(full.status, "value") else str(full.status),
                "tags": list(full.tags or []),
                "derived_from": list(full.derived_from or []),
                "content": full.content or "",
                "relationships": {
                    k: [{"target": e.target, "role": e.role} for e in v]
                    for k, v in (full.relationships or {}).items()
                },
                "archived_at": datetime.now(timezone.utc).isoformat(),
                "reason": "deprecated_by_cleanup",
            }

            snapshot_path = deleted_dir / f"{bug.id}_{ts}.json"
            snapshot_path.write_text(_json.dumps(snapshot, indent=2, ensure_ascii=False))

            # Remove the .md file from the active tree
            storage.delete_element(bug.id)
            archived += 1
            _log.info("cleanup_archived_bug", bug_id=bug.id, snapshot=str(snapshot_path))
        except Exception as exc:
            errors += 1
            _log.error(
                "cleanup_archive_bug_error", bug_id=bug.id, error=str(exc)
            )

    return {
        "status": "ok",
        "archived": archived,
        "errors": errors,
        "remaining": len(bugs) - archived,
    }


async def notify_analysts_confirmed_tool(
    storage: StorageAdapter,
    project_path: str = "",
) -> dict:
    """Push confirmed SRC-BUG-* elements to analyst-manager for spec review.

    After a bug is fixed and confirmed by QA, analysts must verify that
    the essence of the fix is recorded in the structured requirements.
    Once verified, the analyst sets the bug status to 'deprecated',
    which allows cleanup_fixed_bugs to safely delete it.

    This prevents losing implementation knowledge when bugs are deleted.
    """
    from src.agents.task_queue import AbstractTaskQueue
    from src.agents.events import get_queue_url

    elements = storage.list_all()
    confirmed = [
        e
        for e in elements
        if e.id.startswith("SRC-BUG-") and getattr(e, "status", "") == "confirmed"
    ]

    if not confirmed:
        return {"status": "ok", "notified": 0, "message": "No confirmed bugs"}

    pp = Path(project_path) if project_path else Path(".")
    queue_url = get_queue_url(pp)
    queue = AbstractTaskQueue.connect(queue_url)
    await queue.connect()

    notified = 0
    errors = 0
    for bug in confirmed:
        try:
            # Resolve the affected requirement
            affected_id = (
                bug.derived_from[0]
                if (hasattr(bug, "derived_from") and bug.derived_from)
                else ""
            )
            await queue.push(
                "analyst-manager",
                {
                    "action": "review_confirmed_bug",
                    "bug_id": bug.id,
                    "bug_title": bug.title,
                    "bug_content": (getattr(bug, "content", None) or "")[:3000],
                    "affected_requirement": affected_id,
                    "instruction": (
                        f"Bug {bug.id} has been fixed and confirmed by QA. "
                        f"Review the fix and ensure the implementation details "
                        f"are reflected in the structured requirements (spec elements). "
                        f"Update the spec if needed, then set {bug.id} status to 'deprecated'."
                    ),
                },
            )
            notified += 1
        except Exception as exc:
            errors += 1

    await queue.close()
    return {
        "status": "ok",
        "notified": notified,
        "errors": errors,
        "bugs": [b.id for b in confirmed],
    }


# ======================================================================
# ToolDef — JSON Schema for function calling
# ======================================================================


RO_TOOLS: list[ToolDef] = [
    ToolDef(
        name="read_element",
        description="[Spec] Read an element by ID. Use deep=true to also get children, parent, and relationship targets in one call. Returns aspect, element_type, title, status, parent, children, relationships, content.",
        parameters=_params(
            {
                "element_id": {"type": "string", "description": "Element ID"},
                "deep": {
                    "type": "boolean",
                    "description": "If true, also returns children, parent, and relationship targets (full context in one call)",
                },
            },
            ["element_id"],
        ),
    ),
    ToolDef(
        name="list_aspect",
        description="[Spec] List all elements of an aspect with optional pagination (REQ-003).",
        parameters=_params(
            {
                "aspect_name": {"type": "string", "description": "Aspect name"},
                "offset": {
                    "type": "integer",
                    "description": "Number of results to skip (default 0)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 0 = all)",
                },
            },
            ["aspect_name"],
        ),
    ),
    ToolDef(
        name="list_all_elements",
        description="[Spec] List all project elements with optional pagination (REQ-003).",
        parameters=_params(
            {
                "offset": {
                    "type": "integer",
                    "description": "Number of results to skip (default 0)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 0 = all)",
                },
            },
        ),
    ),
    ToolDef(
        name="search_elements",
        description="[Spec] Full-text search by ID, title, and content with optional pagination (REQ-003).",
        parameters=_params(
            {
                "query": {"type": "string", "description": "Search query"},
                "offset": {
                    "type": "integer",
                    "description": "Number of results to skip (default 0)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 0 = all)",
                },
            },
            ["query"],
        ),
    ),
    ToolDef(
        name="find_related",
        description="[Spec] Find elements related to the specified one.",
        parameters=_params(
            {"element_id": {"type": "string", "description": "Element ID"}},
            ["element_id"],
        ),
    ),
    ToolDef(
        name="get_methodology",
        description="[Spec] Get the methodology description.",
        parameters=_params({}),
    ),
    ToolDef(
        name="run_validate",
        description="[Spec] Run MCP specification validation.",
        parameters=_params({}),
    ),
    ToolDef(
        name="run_metrics",
        description="[Spec] Compute connectivity metrics.",
        parameters=_params({}),
    ),
    ToolDef(
        name="get_context_for_file",
        description="[Spec] Build specification context for a code file. Parses @implements annotations and loads referenced requirements with related elements.",
        parameters=_params(
            {
                "file_path": {"type": "string", "description": "Path to the code file"},
            },
            ["file_path"],
        ),
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
        description="Delete an element by ID. Pass force=true to bypass SRC protection.",
        parameters=_params(
            {
                "element_id": {"type": "string", "description": "Element ID"},
                "force": {
                    "type": "boolean",
                    "description": "Bypass SRC deletion protection (VSCode UI uses this)",
                },
            },
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
        name="cleanup_fixed_bugs",
        description="Delete SRC-BUG-* elements with status='deprecated' (analyst-approved for deletion). Only deprecated bugs are safe to delete — their implementation knowledge has been recorded in the spec.",
        parameters=_params({}),
    ),
    ToolDef(
        name="notify_analysts_confirmed",
        description="Push confirmed SRC-BUG-* elements to analyst-manager for spec review. Analysts verify the fix is reflected in requirements, then deprecate the bug.",
        parameters=_params({}),
    ),
    ToolDef(
        name="fix_bugs_parallel",
        description="Push all active SRC-BUG-* elements to the coding agent Redis queue for parallel processing by multiple workers.",
        parameters=_params(
            {
                "model": {"type": "string", "description": "LLM model for coding"},
                "max_parallel": {"type": "integer", "description": "Max concurrent tasks"},
            }
        ),
    ),
    ToolDef(
        name="request_clarification",
        description="Request clarification from a person.",
        parameters=_params({"question": {"type": "string"}}, ["question"]),
    ),
    ToolDef(
        name="set_log_config",
        description="Update runtime logging configuration. Control which modules/elements are logged during a debug cycle. Changes take effect within 5 seconds.",
        parameters=_params(
            {
                "level": {"type": "string", "description": "Minimum severity: debug, info, warning, error (empty = no change)"},
                "modules": {"type": "array", "items": {"type": "string"}, "description": "Only log these MOD-* IDs (empty list = log all, null = no change)"},
                "elements": {"type": "array", "items": {"type": "string"}, "description": "Only log for these element IDs (empty list = log all, null = no change)"},
                "silenced": {"type": "array", "items": {"type": "string"}, "description": "Never log these MOD-* IDs (empty list = none silenced, null = no change)"},
            }
        ),
    ),
]

RW_TOOLS.extend(QUESTIONS_RW_TOOLS)

# Cycle tools (Phase 2-5)
CYCLE_TOOLS: list[ToolDef] = [
    ToolDef(
        name="run_cycle",
        description="[Cycle] Run the full cycle: collect logs, find bugs, create SRC-BUG-* elements, update specification. Returns summary with bugs found and spec changes.",
        parameters=_params(
            {
                "project_path": {
                    "type": "string",
                    "description": "Path to spec-editor project",
                },
                "logs_path": {
                    "type": "string",
                    "description": "Path to application logs directory (default: logs/)",
                },
                "modules": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of module IDs to process",
                },
                "since": {
                    "type": "string",
                    "description": "ISO date to start analysis from",
                },
                "analyze_only": {
                    "type": "boolean",
                    "description": "Stop after analysis phase",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview without writing",
                },
            }
        ),
    ),
    ToolDef(
        name="run_log_analysis",
        description="[Cycle] Analyze structured production logs and generate bug reports. Use before ingest_bugs.",
        parameters=_params(
            {
                "project_path": {"type": "string", "description": "Path to project"},
                "since": {"type": "string", "description": "ISO date"},
                "module_id": {
                    "type": "string",
                    "description": "Optional module filter",
                },
            }
        ),
    ),
    ToolDef(
        name="ingest_bugs",
        description="[Cycle] Convert bug reports (bugs_*.md) into SRC-BUG-* specification elements.",
        parameters=_params(
            {
                "project_path": {"type": "string", "description": "Path to project"},
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview without writing",
                },
            }
        ),
    ),
    ToolDef(
        name="update_spec_from_bugs",
        description="[Cycle] Update specification from SRC-BUG-* elements. Creates NFR requirements and scenario steps.",
        parameters=_params(
            {
                "bug_id": {
                    "type": "string",
                    "description": "Specific bug to process (optional)",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview without writing",
                },
            }
        ),
    ),
]

# Only add cycle tools if the module is available
RW_TOOLS.extend(CYCLE_TOOLS)


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
        "read_element": lambda element_id, deep=False: read_element(
            storage, element_id, deep
        ),
        "list_aspect": lambda aspect_name, offset=0, limit=0: list_aspect(
            storage, aspect_name, offset=offset, limit=limit
        ),
        "list_all_elements": lambda offset=0, limit=0: list_all_elements(
            storage, offset=offset, limit=limit
        ),
        "search_elements": lambda query, offset=0, limit=0: search_elements(
            storage, query, offset=offset, limit=limit
        ),
        "find_related": lambda element_id: find_related(storage, element_id),
        "get_methodology": lambda: get_methodology_tool(methodology),
        "run_validate": lambda: run_validate_tool(storage, methodology),
        "run_metrics": lambda: run_metrics_tool(storage),
        "get_context_for_file": lambda file_path="": get_context_for_file_tool(
            storage, file_path
        ),
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
    ci_threshold: float = 0.7,
) -> dict[str, Callable]:
    """Build a handler dict for all tools (read + write)."""
    _sd = source_dir or ""

    handlers = build_read_only_handlers(
        storage, methodology, source_dir, spawner, agent_for_compact, srs_template_path
    )
    handlers.update(
        {
            "write_element": lambda **kw: write_element_tool(
                storage, methodology, **kw
            ),
            "delete_element": lambda element_id, force=False: delete_element_tool(
                storage, element_id, force
            ),
            "add_relationship": lambda **kw: add_relationship_tool(
                storage, **kw, methodology=methodology
            ),
            "remove_relationship": lambda **kw: remove_relationship_tool(storage, **kw),
            "report_complete": lambda: report_complete(storage, ci_threshold),
            "escalate": lambda reason: escalate(reason),
            "request_clarification": lambda question: request_clarification(question),
            "cleanup_fixed_bugs": lambda: cleanup_fixed_bugs_tool(storage),
            "notify_analysts_confirmed": lambda: notify_analysts_confirmed_tool(
                storage,
                project_path=str(getattr(storage, "_project_path", "")),
            ),
            "fix_bugs_parallel": lambda model="", max_parallel=5: fix_bugs_parallel_tool(
                storage,
                project_path=str(getattr(storage, "_project_path", "")),
                model=model or DEFAULT_REASONING_MODEL,
                max_parallel=max_parallel,
            ),
        }
    )
    add_question_tools_handlers(handlers, _sd)

    # ── Runtime logging control ──
    handlers["set_log_config"] = (
        lambda level="", modules=None, elements=None, silenced=None: set_log_config_tool(
            level=level, modules=modules, elements=elements, silenced=silenced
        )
    )

    # Plugin-provided tools (discovered via hooks, e.g., cycle tools).
    # Derive project path from source_dir: source_dir is typically
    #   "{project_path}/source", so parent is the project root.
    try:
        from pathlib import Path

        from src.hooks import get_plugins

        pp = str(Path(_sd).parent) if _sd else ""
        for plugin in get_plugins():
            try:
                extra = plugin.register_mcp_tools(storage, pp)
                if extra:
                    handlers.update(extra)
            except Exception:
                pass
    except ImportError:
        pass

    return handlers

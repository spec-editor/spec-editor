"""Project-level MCP tools that use the EditorAdapter.

These tools bridge the gap between the MCP server and editor-specific
functionality: git history, diagram generation, file tree browsing.

References:
    SRC-UI-ADAPTER: EditorAdapter for VSCode/Zed/standalone integration
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.ui.adapters.base import IEditorAdapter

# =============================================================================
# git_history — history of file/directory changes
# =============================================================================


async def git_history_tool(
    adapter: IEditorAdapter,
    file_path: str = "",
    element_id: str = "",
    max_count: int = 50,
) -> dict[str, Any]:
    """Get git history for a file or directory.

    Args:
        file_path: Path to file (relative to project). If empty, uses the
                   element_id to resolve the path.
        element_id: If file_path is empty, resolves the element's file.
        max_count: Max number of commits to return (default 50).
    """
    if not file_path and not element_id:
        return {"error": "Either file_path or element_id is required"}

    target_path = Path(file_path) if file_path else None
    if target_path is None:
        # Resolve element_id to a file path (handled by the caller via storage)
        return {"error": "element_id resolution not available—use file_path"}

    try:
        commits = adapter.git_history(target_path, max_count=max_count)
    except Exception as exc:
        return {"error": str(exc)}

    return {
        "file": str(target_path),
        "commits": [
            {
                "hash": c.hash,
                "author": c.author,
                "date": c.date,
                "message": c.message,
            }
            for c in commits
        ],
        "count": len(commits),
    }


# =============================================================================
# git_diff — current diff for a file
# =============================================================================


async def git_diff_tool(
    adapter: IEditorAdapter,
    file_path: str = "",
) -> dict[str, Any]:
    """Get the current unstaged diff for a file.

    Args:
        file_path: Path to file (relative to project).
    """
    if not file_path:
        return {"error": "file_path is required"}

    try:
        diff = adapter.git_diff(Path(file_path))
    except Exception as exc:
        return {"error": str(exc)}

    return {"file": file_path, "diff": diff}


# =============================================================================
# git_branches — list branches
# =============================================================================


async def git_branches_tool(adapter: IEditorAdapter) -> dict[str, Any]:
    """List git branches in the current project."""
    try:
        branches = adapter.git_branches()
    except Exception as exc:
        return {"error": str(exc)}

    return {"branches": branches, "count": len(branches)}


# =============================================================================
# get_file_tree — project file structure
# =============================================================================


async def get_file_tree_tool(
    adapter: IEditorAdapter,
    path: str = "",
) -> dict[str, Any]:
    """Show the project file structure (code + spec files).

    Args:
        path: Directory path to show (default: current project root).
    """
    from src.config import get_logger

    _ = get_logger(__name__)  # keep import, used in module

    target = Path(path) if path else None
    if target is None:
        return {"error": "path is required"}

    if not target.is_dir():
        return {"error": f"Directory not found: {target}"}

    try:
        files = adapter.walk_directory(target)
    except Exception as exc:
        return {"error": str(exc)}

    entries = sorted(str(f) for f in files)

    return {
        "root": str(target),
        "file_count": len(entries),
        "files": entries[:200],
        "truncated": len(entries) > 200,
    }


# =============================================================================
# generate_diagram — Mermaid diagram from relationships
# =============================================================================


async def generate_diagram_tool(
    project_path: str = "",
    aspect_name: str = "",
    diagram_type: str = "graph",
    node_path: str = "",
    relation_scope: str = "",
) -> dict[str, Any]:
    """Generate a Mermaid diagram for a specification aspect.

    Args:
        project_path: Path to spec-editor project directory.
        aspect_name: Aspect to diagram (default: all aspects).
        diagram_type: "graph" (default) — currently only graph is supported.
        node_path: Specific element ID to focus on (optional).
        relation_scope: "internal" (same-aspect edges only),
                       "external" (cross-aspect edges only),
                       "" (all edges, default).
    """
    from src.view.renderer import MermaidRenderer

    if not project_path:
        return {"error": "project_path is required", "diagram": ""}

    pp = Path(project_path)
    if not pp.is_dir():
        return {"error": f"Project directory not found: {project_path}", "diagram": ""}

    try:
        renderer = MermaidRenderer()
        mermaid_code = renderer.build_mermaid(
            pp,
            element_id=node_path or None,
            diagram_type=diagram_type,
            aspect_name=aspect_name or None,
            relation_scope=relation_scope or None,
        )

        # Count elements for metadata
        elements = renderer._load_elements(pp)
        if aspect_name:
            elements = [e for e in elements if e.get("aspect") == aspect_name]

        # Basic syntax validation
        syntax_error = _validate_mermaid_syntax(mermaid_code, diagram_type)
        if syntax_error:
            return {"error": syntax_error, "diagram": mermaid_code}

        return {
            "diagram": mermaid_code,
            "diagram_type": diagram_type,
            "aspect": aspect_name or "all",
            "element_count": len(elements),
        }
    except Exception as exc:
        return {"error": str(exc), "diagram": ""}


# =============================================================================
# list_diagram_types — available diagram types per aspect
# =============================================================================


async def list_diagram_types_tool(aspect: str = "") -> dict[str, Any]:
    """List available diagram types for a given aspect (or all).

    Args:
        aspect: Aspect name (default: all aspects).
    """
    types = [
        {
            "type": "graph",
            "description": ("Graph diagram showing elements and their relationships"),
            "supports_focus": True,
            "supports_aspect_filter": True,
        },
        {
            "type": "cycle",
            "description": (
                "Cycle: bug → requirement → code → logs → bug. "
                "Focus on SRC-BUG-* or MOD-* element."
            ),
            "supports_focus": True,
            "supports_aspect_filter": False,
        },
    ]

    return {
        "diagram_types": types,
        "aspect": aspect or "all",
        "default_type": "graph",
    }


# =============================================================================
# Internal helpers
# =============================================================================


def _validate_mermaid_syntax(code: str, diagram_type: str) -> str:
    """Validate basic Mermaid syntax. Returns error message or empty string."""
    if not code or not code.strip():
        return "Empty diagram"
    lines = code.strip().split("\n")
    first = lines[0].strip()

    # Check diagram type header
    valid_headers = {
        "graph": ("graph", "flowchart"),
        "flowchart": ("graph", "flowchart"),
        "classDiagram": ("classDiagram",),
        "class": ("classDiagram",),
        "erDiagram": ("erDiagram",),
        "er": ("erDiagram",),
        "stateDiagram": ("stateDiagram-v2", "stateDiagram"),
        "state": ("stateDiagram-v2", "stateDiagram"),
        "sequenceDiagram": ("sequenceDiagram",),
        "sequence": ("sequenceDiagram",),
        "gantt": ("gantt",),
        "pie": ("pie",),
        "mindmap": ("mindmap",),
        "timeline": ("timeline",),
        "sankey-beta": ("sankey-beta",),
    }
    expected = valid_headers.get(diagram_type, (diagram_type,))
    if not any(first.startswith(h) for h in expected):
        return f"Unexpected diagram header: {first} (expected one of {expected})"

    # Check balanced quotes
    if code.count('"') % 2 != 0:
        return "Unbalanced double quotes"

    # Check balanced braces, accounting for Mermaid ER syntax:
    # ER relationship notation uses { and } in ||--o{ , }o--|| , }o--o{
    # Only count braces on non-relationship lines.
    if diagram_type in ("er", "erDiagram"):
        non_rel_lines = [ln for ln in lines if "--" not in ln]
        non_rel_code = "\n".join(non_rel_lines)
        if non_rel_code.count("{") != non_rel_code.count("}"):
            return f"Unbalanced braces: {non_rel_code.count('{')} open, {non_rel_code.count('}')} close"
    else:
        if code.count("{") != code.count("}"):
            return f"Unbalanced braces: {code.count('{')} open, {code.count('}')} close"

    return ""  # OK


def _build_tree(
    files: list[Path], max_depth: int = 3, max_items: int = 200
) -> list[str]:
    """Build a text tree from a list of file paths."""
    lines: list[str] = []
    tree: dict[str, list[str]] = {}
    for f in files:
        parts = str(f).replace("\\", "/").split("/")
        current = tree
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                current.setdefault("__files__", []).append(part)
            else:
                if part not in current:
                    current[part] = {}
                current = current[part]

    _render_tree(tree, lines, "", max_depth, max_items)
    return lines


def _render_tree(
    node: dict,
    lines: list[str],
    prefix: str,
    max_depth: int,
    max_items: int,
    depth: int = 0,
) -> None:
    """Recursive tree rendering helper."""
    if depth > max_depth or len(lines) >= max_items:
        return

    entries = sorted(
        [(k, v) for k, v in node.items() if k != "__files__"],
        key=lambda x: (isinstance(x[1], dict), x[0]),
    )
    file_parts = sorted(node.get("__files__", []))

    for i, (name, sub) in enumerate(entries):
        is_last = i == len(entries) - 1 and not file_parts
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{name}/")
        if isinstance(sub, dict):
            new_prefix = prefix + ("    " if is_last else "│   ")
            _render_tree(sub, lines, new_prefix, max_depth, max_items, depth + 1)
        if len(lines) >= max_items:
            return

    for i, name in enumerate(file_parts):
        is_last = i == len(file_parts) - 1
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{name}")
        if len(lines) >= max_items:
            return


# =============================================================================
# is_run_active — check if spec-editor run is in progress
# =============================================================================


async def is_run_active_tool(project_path: str = "") -> dict[str, Any]:
    """Check if spec-editor run is currently executing in the project.

    Looks for .spec-editor-running lock file in the project root.
    The lock file contains the PID of the running process and is
    created by spec-editor run, deleted on completion.

    Args:
        project_path: Path to spec-editor project directory.
    """
    if not project_path:
        return {"active": False, "reason": "No project path provided"}

    lock_file = Path(project_path) / ".spec-editor-running"

    if not lock_file.exists():
        return {"active": False}

    try:
        pid_str = lock_file.read_text().strip()
        pid = int(pid_str)
    except (ValueError, OSError):
        # Stale or corrupted lock file — clean it up
        lock_file.unlink(missing_ok=True)
        return {"active": False, "reason": "Stale lock file removed"}

    # Verify the process is still alive
    from src.utils import is_process_running

    if not is_process_running(pid):
        lock_file.unlink(missing_ok=True)
        return {"active": False, "reason": f"PID {pid} no longer running"}

    # Read progress metrics for status bar display
    try:
        from src.mcp.metrics import compute_metrics
        from src.storage.filesystem import FilesystemStorage

        storage = FilesystemStorage(Path(project_path))
        m = compute_metrics(storage)
        return {
            "active": True,
            "pid": pid,
            "elements": m.total_elements,
            "relationships": m.total_relationships,
            "connectivity": round(m.connectivity_index, 2),
            "orphans": m.orphan_elements,
        }
    except Exception:
        pass

    return {"active": True, "pid": pid}

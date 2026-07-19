"""MCP Server — stdio/json-rpc and HTTP transports for external agents.

Launch: spec-editor mcp [-p <project>] [--transport stdio|http] [--port PORT] [--read-only]

Supports:
  - stdio transport (default): JSON-RPC via stdin/stdout, full read+write
  - http transport: HTTP POST /mcp JSON-RPC, optional --read-only

Connects third-party MCP clients (Cursor, Claude Desktop, Zed) to storage.

IMPORTANT: structlog must be configured BEFORE any project imports,
because module-level get_logger() calls trigger auto-configuration.
"""

# ── Constants ──
_DEFAULT_MCP_PORT = 8088

# Planka access token for iframe proxy (auto-login)
PLANKA_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6IjU0ODBmYTYwLTE0OWUtNDQ1Ni1iY2Q3LWUyNmE1MTQ0MTU3MSJ9.eyJpYXQiOjE3ODMyNTM1MDksImV4cCI6MTgxNDc4OTUwOSwic3ViIjoiMTgxMjQyMTY4NDA4MzI5NTIzMyJ9.RFpr-ZVGapHgfJgdN2RA2oyA0YDq0JcGwebX_G7NwxI"

# Performance thresholds (REQ-001)
_READ_THRESHOLD_MS = 200
_WRITE_THRESHOLD_MS = 500
# REQ-003: 1-second threshold for scalability-critical operations
_SCALABILITY_THRESHOLD_MS = 1000
_SCALABILITY_TOOLS = frozenset(
    {
        "list_all_elements",
        "list_aspect",
        "search_elements",
        "read_element",
    }
)
_READ_TOOLS = frozenset(
    {
        "read_element",
        "list_aspect",
        "list_all_elements",
        "search_elements",
        "find_related",
        "get_methodology",
        "run_validate",
        "run_metrics",
        "get_context_for_file",
        "git_history",
        "git_diff",
        "git_branches",
        "get_file_tree",
        "generate_diagram",
        "list_diagram_types",
        "generate_local_diagram",
        "analyze_image",
        "is_run_active",
        "search_semantic",
    }
)
_WRITE_TOOLS = frozenset(
    {
        "write_element",
        "add_relationship",
        "delete_element",
        "remove_relationship",
    }
)


def _log_perf(tool_name: str, elapsed_ms: float) -> None:
    """Log a performance warning if the tool exceeded its threshold (REQ-001/REQ-003)."""
    if tool_name in _READ_TOOLS and elapsed_ms > _READ_THRESHOLD_MS:
        logger.warning(
            "perf_slow_read",
            tool=tool_name,
            elapsed_ms=f"{elapsed_ms:.1f}",
            threshold_ms=_READ_THRESHOLD_MS,
        )
    elif tool_name in _WRITE_TOOLS and elapsed_ms > _WRITE_THRESHOLD_MS:
        logger.warning(
            "perf_slow_write",
            tool=tool_name,
            elapsed_ms=f"{elapsed_ms:.1f}",
            threshold_ms=_WRITE_THRESHOLD_MS,
        )
    if tool_name in _SCALABILITY_TOOLS and elapsed_ms > _SCALABILITY_THRESHOLD_MS:
        logger.warning(
            "perf_scalability_exceeded_req003",
            tool=tool_name,
            elapsed_ms=f"{elapsed_ms:.1f}",
            threshold_ms=_SCALABILITY_THRESHOLD_MS,
        )


# ── Configure logging FIRST, before any project imports ──
import logging
import sys

for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)
logging.basicConfig(stream=sys.stderr, level=logging.ERROR, format="%(message)s")

import structlog

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(colors=False),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.stdlib.LoggerFactory(),
)

# ── Now safe to import project modules ──
import contextlib
import time

from src.config import get_logger

logger = get_logger(__name__)
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from src.agents.tools import (
    build_all_handlers,
    build_read_only_handlers,
    get_tool_definitions,
)
from src.config.engine import MethodologyEngine
from src.config.methodology import load_methodology
from src.mcp.sse import SseHub
from src.storage.filesystem import FilesystemStorage
from src.tracing import implements
from src.ui.adapters.base import IEditorAdapter
from src.ui.adapters.standalone import StandaloneAdapter

# ======================================================================
# Core MCP logic (transport-agnostic)
# ======================================================================


class MCPHandler:
    """Handles JSON-RPC MCP requests independently of transport.

    Supports multiple projects simultaneously — each tool call includes
    a ``project_path`` argument to identify which project to operate on.
    State (storage, methodology, handlers) is cached per project path.
    """

    def __init__(
        self,
        project_path: Path | None = None,
        writable: bool = True,
        adapter: IEditorAdapter | None = None,
    ) -> None:
        self._writable = writable
        self._adapter = adapter or StandaloneAdapter()
        self._sse_hub = SseHub()
        self._storage: Any = None
        # ── Per-project state: key = resolved project path string ──
        self._states: dict[str, dict[str, Any]] = {}
        # Legacy single-project init (for backward compat)
        if project_path:
            self._get_state(str(project_path.resolve()))

    # ── State management ──

    def _get_state(self, project_path: str) -> dict[str, Any]:
        """Get or lazily initialize state for a project path."""
        resolved = str(Path(project_path).resolve())
        if resolved not in self._states:
            self._init_state_for(Path(resolved))
        return self._states[resolved]

    @staticmethod
    def _resolve_project_path(host_path: str) -> Path | None:
        """Map a host fullpath to the container-accessible project path.

        Architecture:
          The extension passes host fullpaths (e.g. /Users/.../Documents/Droid/<project>)
          read from local.yaml's ``project_path`` field. The MCP server runs inside a
          Docker container where host paths do not exist — the host directory is mounted
          at a container path (e.g. /projects).

          This method applies a configured prefix mapping to convert host paths
          to container paths. The mapping is defined by two environment variables:

            SPEC_EDITOR_MOUNT_HOST      — host directory mounted into the container
            SPEC_EDITOR_MOUNT_CONTAINER — corresponding path inside the container

          Example:
            Host path:      /workspace/my-project
            Mount host:     /workspace
            Mount container: /projects
            Result:         /projects/my-project

          If no env vars are configured, falls back to legacy behaviour:
          /projects/<basename>.

        Returns:
            Resolved Path if methodology.yaml is found, None otherwise.
        """
        pp = Path(host_path)
        if (pp / "methodology.yaml").is_file():
            return pp

        mount_host = os.environ.get("SPEC_EDITOR_MOUNT_HOST", "")
        mount_container = os.environ.get("SPEC_EDITOR_MOUNT_CONTAINER", "")
        remapped = None
        if mount_host and mount_container and host_path.startswith(mount_host):
            rel = host_path[len(mount_host):].lstrip("/")
            remapped = Path(mount_container) / rel
        if remapped is None:
            # Legacy fallback: try /projects/<basename>
            remapped = Path("/projects") / pp.name
        if (remapped / "methodology.yaml").is_file():
            return remapped
        return None

    def _init_state_for(self, pp: Path) -> None:
        """Load storage, methodology, and build handlers for a project."""
        original_key = str(pp)
        resolved = self._resolve_project_path(str(pp))
        if resolved is None:
            raise FileNotFoundError(f"methodology.yaml not found in {pp}")
        pp = resolved

        storage = FilesystemStorage(pp)
        method_path = pp / "methodology.yaml"
        engine = MethodologyEngine.from_path(method_path) if method_path.exists() else None
        if engine is None:
            raise FileNotFoundError(f"methodology.yaml not found in {pp}")
        source_dir = str(pp / "source")

        # ── Build agent tool handlers ──
        handlers: dict[str, Callable] = {}
        try:
            if self._writable:
                handlers = build_all_handlers(
                    storage, engine, source_dir
                )
            else:
                handlers = build_read_only_handlers(
                    storage, engine, source_dir
                )
        except Exception:
            pass

        # Inject project-level tools
        self._inject_project_tools(handlers, pp, storage)

        state = {
            "storage": storage,
            "methodology": engine,
            "handlers": handlers,
            "source_dir": source_dir,
            "project_path": str(pp),
        }
        # Store under original host key so _get_state can find it
        self._states[original_key] = state
        # Also store under resolved container path for direct lookups
        if str(pp) != original_key:
            self._states[str(pp)] = state

        logger.info("mcp_project_loaded", path=str(pp), elements=len(storage.list_all()))

    def _inject_project_tools(
        self, handlers: dict, pp: Path, storage: Any
    ) -> None:
        """Inject project-level tools into the handler dict."""
        adapter = self._adapter

        handlers["get_file_tree"] = lambda path="", **kw: _get_file_tree_sync(
            adapter, path or str(pp)
        )
        handlers["git_history"] = lambda file_path="", element_id="", max_count=50, **kw: _git_history_sync(
            adapter, storage, str(pp), file_path, element_id, max_count
        )
        handlers["git_diff"] = lambda file_path="", element_id="", **kw: _git_diff_sync(
            adapter, storage, str(pp), file_path, element_id
        )
        handlers["git_branches"] = lambda **kw: _git_branches_sync(adapter)
        handlers["generate_diagram"] = lambda aspect="", diagram_type="graph", node_path="", relation_scope="", **kw: _generate_diagram_sync(
            str(pp), storage, aspect, diagram_type, node_path, relation_scope
        )
        handlers["generate_local_diagram"] = lambda aspect="", node_path="", **kw: _generate_local_diagram_sync(
            str(pp), storage, aspect, node_path
        )
        handlers["is_run_active"] = lambda **kw: _is_run_active_sync(str(pp))

    # ── Legacy compat (for code that references self._state directly) ──

    @property
    def _state(self) -> dict[str, Any]:
        """Return the first loaded project's state (backward compat)."""
        if self._states:
            return next(iter(self._states.values()))
        return {"storage": None, "handlers": {}, "source_dir": "", "project_path": ""}

    # ── Request dispatch ──

    @property
    def sse_hub(self) -> SseHub:
        """SSE hub for push notifications to connected clients."""
        return self._sse_hub

    def handle_request(self, method: str, params: dict | None = None) -> dict:
        """Handle a single JSON-RPC method call. Returns result dict."""
        params = params or {}

        if method == "initialize":
            return self._do_initialize()

        if method == "tools/list":
            return {"tools": self._build_schemas()}

        if method == "tools/call":
            return self._dispatch_tool(params)

        return self._error(f"Unknown method: {method}")

    def _do_initialize(self) -> dict:
        """Return initialize response with version from VERSION file."""
        from importlib.metadata import version as get_version

        try:
            pkg_version = get_version("spec-editor")
        except Exception:
            # Fallback: read from VERSION file
            version_file = Path(__file__).parent.parent.parent / "VERSION"
            try:
                pkg_version = version_file.read_text().strip()
            except Exception:
                pkg_version = "0.1.0"

        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
                "resources": {},
            },
            "serverInfo": {
                "name": "spec-editor-mcp",
                "version": pkg_version,
                "editor": self._adapter.editor_name(),
            },
        }

    def _dispatch_tool(self, params: dict) -> dict:
        """Dispatch a tools/call request to the appropriate handler.

        All spec-level tools require ``project_path`` in arguments.
        Stateless tools (list_projects, analyze_image) are exempt.
        """
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        # ── Stateless tools (no project needed) ──
        if tool_name == "list_projects":
            return self._handle_list_projects(arguments)
        if tool_name == "get_project_info":
            return self._handle_get_project_info(arguments)
        if tool_name == "analyze_image":
            return self._handle_analyze_image(arguments)
        if tool_name == "list_diagram_types":
            return self._handle_list_diagram_types(arguments)

        # ── All other tools require project_path ──
        project_path = arguments.get("project_path", "")
        if not project_path:
            # Auto-detect: use the first loaded project (or the only one)
            if self._states:
                project_path = next(iter(self._states.keys()))
            else:
                return self._error(
                    "project_path is required. Specify the path to a spec-editor "
                    "project (directory containing methodology.yaml)."
                )

        try:
            state = self._get_state(project_path)
        except Exception as exc:
            return self._error(
                f"Failed to load project at '{project_path}': {exc}"
            )

        handlers = state.get("handlers", {})

        # Strip project_path from arguments before passing to handlers
        tool_args = {k: v for k, v in arguments.items() if k != "project_path"}

        # Auto-fill code_dir from project_path for code tools that need it
        _code_tools = {"search_symbol", "search_semantic", "search_code", "get_file_tree", "read_lints",
                       "annotate_code", "verify_implements", "verify_traceability"}
        if tool_name in _code_tools and not tool_args.get("code_dir") and project_path:
            tool_args["code_dir"] = project_path

        handler = handlers.get(tool_name)
        if handler:
            try:
                import asyncio
                import traceback

                # ── Auth: set caller context for write operations ──
                _WRITE_TOOLS = {"write_element", "delete_element", "add_relationship", "remove_relationship"}
                if tool_name in _WRITE_TOOLS:
                    from src.agents.tools import set_tool_caller
                    caller = arguments.get("_caller", "mcp-anonymous")
                    set_tool_caller(caller, project_path)

                result = handler(**tool_args)
                if asyncio.iscoroutine(result):
                    result = asyncio.run(result)

                # Fire SSE events for write operations
                if tool_name in ("write_element", "delete_element"):
                    element_id = tool_args.get("element_id", "")
                    if tool_name == "write_element":
                        element_id = tool_args.get("id", "")
                    self._sse_hub.notify(
                        "element_updated",
                        {
                            "action": tool_name,
                            "elementId": element_id,
                            "aspect": tool_args.get("aspect", ""),
                            "project_path": project_path,
                        },
                    )
                elif tool_name in ("add_relationship", "remove_relationship"):
                    self._sse_hub.notify(
                        "relationship_updated",
                        {
                            "action": tool_name,
                            "sourceId": tool_args.get("source_id", ""),
                            "targetId": tool_args.get("target_id", ""),
                            "relType": tool_args.get("rel_type", ""),
                            "project_path": project_path,
                        },
                    )

                return self._content(result)
            except Exception as exc:
                import sys
                print(f"[MCP ERROR] {tool_name}: {exc}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                return self._error(str(exc))
        else:
            return self._error(
                f"Unknown tool '{tool_name}' for project '{project_path}'."
            )

    # ── Stateless tool handlers (no project needed) ──

    def _handle_list_projects(self, arguments: dict) -> dict:
        """List discovered spec-editor projects with methodology summaries."""
        base_dir_str = arguments.get("base_dir", "")
        base_dir = Path(base_dir_str) if base_dir_str else None
        try:
            projects = self._adapter.find_projects(base_dir)
        except Exception as exc:
            return self._error(str(exc))

        # Enrich with methodology details
        enriched = []
        for p in projects:
            info: dict = {
                "path": str(p.path),
                "name": str(p.path),  # Full path — same value accepted as project_path
                "methodology": p.methodology,
                "element_count": p.element_count,
            }
            # Try to load methodology.yaml for version + aspect count
            try:
                import yaml
                mpath = Path(p.path) / "methodology.yaml"
                if mpath.is_file():
                    raw = yaml.safe_load(mpath.read_text()) or {}
                    info["methodology_version"] = str(raw.get("version", "?"))
                    info["aspects_count"] = len(raw.get("aspects", []))
            except Exception:
                pass
            enriched.append(info)

        return self._content({"projects": enriched, "count": len(enriched)})

    def _handle_get_project_info(self, arguments: dict) -> dict:
        """Get full methodology + stats for any project (stateless)."""
        pp_str = arguments.get("project_path", "")
        if not pp_str:
            return self._error("project_path is required")

        pp = self._resolve_project_path(pp_str)
        if pp is None:
            return self._error(
                f"Not a spec-editor project: {pp_str} — methodology.yaml not found"
            )

        import yaml
        raw = yaml.safe_load((pp / "methodology.yaml").read_text()) or {}

        # Count elements by status
        element_count = 0
        status_counts: dict[str, int] = {}
        aspects_dir = pp / "aspects"
        if aspects_dir.is_dir():
            for md_file in aspects_dir.rglob("*.md"):
                element_count += 1
                st = "draft"  # default
                try:
                    text = md_file.read_text()
                    if text.startswith("---"):
                        end = text.find("---", 3)
                        if end > 0:
                            fm = yaml.safe_load(text[3:end]) or {}
                            st = str(fm.get("status", "draft"))
                except Exception:
                    pass
                status_counts[st] = status_counts.get(st, 0) + 1

        return self._content({
            "project_path": str(pp),
            "name": str(pp),  # Full path — same value accepted by other tools
            "methodology": raw.get("name", "?"),
            "methodology_version": str(raw.get("version", "?")),
            "description": raw.get("description", ""),
            "aspects": [
                {
                    "name": a.get("name", "?"),
                    "title": a.get("title", ""),
                    "element_types": [
                        {"name": et.get("name", "?"), "title": et.get("title", "")}
                        for et in a.get("element_types", [])
                    ],
                    "relationship_types": [
                        {"name": rt.get("name", "?"), "title": rt.get("title", "")}
                        for rt in a.get("relationship_types", [])
                    ],
                }
                for a in raw.get("aspects", [])
            ],
            "element_count": element_count,
            "status_breakdown": status_counts,
        })

    def _handle_analyze_image(self, arguments: dict) -> dict:
        """Analyze an image using local vision LLM."""
        file_path = arguments.get("file_path", "")
        if not file_path:
            return self._error("file_path is required")

        import asyncio

        result = asyncio.run(_analyze_image_async(file_path))
        return self._content(result)

    def _handle_list_diagram_types(self, arguments: dict) -> dict:
        """List available diagram types."""
        aspect = arguments.get("aspect", "")
        import asyncio
        result = asyncio.run(_list_diagram_types_async(aspect))
        return self._content(result)

    # ── Schema building ──

    def _build_schemas(self) -> list:
        tools = get_tool_definitions(writable=self._writable)
        schemas = [
            {"name": t.name, "description": t.description, "inputSchema": t.parameters}
            for t in tools
        ]

        # Append built-in project tools (all require project_path except stateless)
        builtin_tools = [
            _list_projects_schema(),       # stateless
            _get_project_info_schema(),    # stateless — full methodology for any project
            _analyze_image_schema(),        # stateless
            _list_diagram_types_schema(),   # stateless
            _git_history_schema(),
            _git_diff_schema(),
            _git_branches_schema(),
            _get_file_tree_schema(),
            _generate_diagram_schema(),
            _is_run_active_schema(),
            _generate_local_diagram_schema(),
            _search_semantic_schema(),
        ]
        schemas.extend(
            {
                "name": t["name"],
                "description": t["description"],
                "inputSchema": t["inputSchema"],
            }
            for t in builtin_tools
        )
        return schemas

    # ── Response helpers ──

    @staticmethod
    def _content(data) -> dict:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(data, ensure_ascii=False, default=str),
                }
            ]
        }

    @staticmethod
    def _error(msg: str) -> dict:
        return {"content": [{"type": "text", "text": msg}], "isError": True}


# ======================================================================
# Sync wrappers for project tools (called from _inject_project_tools)
# ======================================================================

import asyncio as _asyncio


def _get_file_tree_sync(adapter, path_str: str) -> dict:
    return _asyncio.run(_get_file_tree_async(adapter, path_str))


def _git_history_sync(adapter, storage, project_path: str, file_path: str, element_id: str, max_count: int) -> dict:
    if not file_path and element_id and storage:
        elem_path = storage.get_element_path(element_id)
        if elem_path:
            file_path = str(Path(project_path) / elem_path)
    if not file_path:
        return {"error": "file_path or element_id required"}
    result = _asyncio.run(_git_history_async(adapter, file_path, max_count))
    if "error" in result:
        return {"error": result["error"]}
    return result


def _git_diff_sync(adapter, storage, project_path: str, file_path: str, element_id: str) -> dict:
    if not file_path and element_id and storage:
        elem_path = storage.get_element_path(element_id)
        if elem_path:
            file_path = str(Path(project_path) / elem_path)
    if not file_path:
        return {"error": "file_path or element_id required"}
    result = _asyncio.run(_git_diff_async(adapter, file_path))
    if "error" in result:
        return {"error": result["error"]}
    return result


def _git_branches_sync(adapter) -> dict:
    result = _asyncio.run(_git_branches_async(adapter))
    if "error" in result:
        return {"error": result["error"]}
    return result


def _generate_diagram_sync(project_path: str, storage, aspect: str, diagram_type: str, node_path: str, relation_scope: str = "") -> dict:
    result = _asyncio.run(_generate_diagram_async(project_path, aspect, diagram_type, node_path, relation_scope))
    if "error" in result:
        return {"error": result["error"]}
    return result


def _generate_local_diagram_sync(project_path: str, storage, aspect: str, node_path: str) -> dict:
    result = _asyncio.run(_generate_local_diagram_async(project_path, storage, aspect, node_path))
    return result


def _is_run_active_sync(project_path: str) -> dict:
    from src.mcp.tools_project import is_run_active_tool
    return _asyncio.run(is_run_active_tool(project_path=project_path))


# ======================================================================
# Built-in tool schemas
# ======================================================================

_PROJECT_PATH_SCHEMA = {
    "project_path": {
        "type": "string",
        "description": "Path to spec-editor project (directory with methodology.yaml)",
    }
}


def _list_projects_schema() -> dict:
    return {
        "name": "list_projects",
        "description": (
            "[Project] List all discovered spec-editor projects with methodology summaries. "
            "Searches for directories containing methodology.yaml. "
            "Returns path, name, methodology name, version, aspects count, element count."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "base_dir": {
                    "type": "string",
                    "description": "Base directory to search (optional)",
                }
            },
        },
    }


def _get_project_info_schema() -> dict:
    return {
        "name": "get_project_info",
        "description": (
            "[Project] Get full methodology + statistics for any project without loading it. "
            "Stateless — works across multiple projects. "
            "Returns methodology name, version, description, all aspects with "
            "element_types and relationship_types, plus element count."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_path": {
                    "type": "string",
                    "description": "Path to spec-editor project directory",
                }
            },
            "required": ["project_path"],
        },
    }


def _git_history_schema() -> dict:
    return {
        "name": "git_history",
        "description": (
            "[Export] Get git commit history for a spec element file. "
            "Returns author, date, message for each commit. Requires project_path."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **_PROJECT_PATH_SCHEMA,
                "file_path": {"type": "string", "description": "Path to file"},
                "element_id": {
                    "type": "string",
                    "description": "Element ID (resolves to file path)",
                },
                "max_count": {
                    "type": "integer",
                    "description": "Max commits (default 50)",
                },
            },
            "required": ["project_path"],
        },
    }


def _git_diff_schema() -> dict:
    return {
        "name": "git_diff",
        "description": (
            "[Export] Get current unstaged git diff for a spec element file. Requires project_path."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **_PROJECT_PATH_SCHEMA,
                "file_path": {"type": "string", "description": "Path to file"},
                "element_id": {
                    "type": "string",
                    "description": "Element ID (resolves to file path)",
                },
            },
            "required": ["project_path"],
        },
    }


def _git_branches_schema() -> dict:
    return {
        "name": "git_branches",
        "description": "[Export] List git branches. Requires project_path.",
        "inputSchema": {
            "type": "object",
            "properties": {
                **_PROJECT_PATH_SCHEMA,
            },
            "required": ["project_path"],
        },
    }


def _get_file_tree_schema() -> dict:
    return {
        "name": "get_file_tree",
        "description": (
            "[Code] Show project file structure. Returns sorted list of all files. "
            "Uses project_path as root directory."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **_PROJECT_PATH_SCHEMA,
                "path": {
                    "type": "string",
                    "description": "Subdirectory path (default: project root)",
                },
            },
            "required": ["project_path"],
        },
    }


def _generate_diagram_schema() -> dict:
    return {
        "name": "generate_diagram",
        "description": (
            "[Export] Generate a Mermaid diagram for a specification aspect. "
            "Returns Mermaid syntax for graph/er/flowchart/class diagrams. Requires project_path."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **_PROJECT_PATH_SCHEMA,
                "aspect": {
                    "type": "string",
                    "description": "Aspect name (default: all aspects)",
                },
                "diagram_type": {
                    "type": "string",
                    "description": (
                        "Diagram type: graph, er, flowchart, class (default: graph)"
                    ),
                },
                "node_path": {
                    "type": "string",
                    "description": "Focus on a specific element ID (optional)",
                },
            },
            "required": ["project_path"],
        },
    }


def _list_diagram_types_schema() -> dict:
    return {
        "name": "list_diagram_types",
        "description": (
            "[Export] List available diagram types for an aspect. "
            "Returns which diagram types are supported."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "aspect": {
                    "type": "string",
                    "description": "Aspect name (optional)",
                },
            },
        },
    }


def _generate_local_diagram_schema() -> dict:
    return {
        "name": "generate_local_diagram",
        "description": (
            "[Export] Generate a Mermaid diagram using local LLM (Ollama). "
            "Runs fully offline on Apple Silicon. No API key needed. "
            "Requires: ollama pull qwen2.5-coder:7b. Requires project_path."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **_PROJECT_PATH_SCHEMA,
                "aspect": {
                    "type": "string",
                    "description": "Aspect name (default: all aspects)",
                },
                "node_path": {
                    "type": "string",
                    "description": "Focus on a specific element ID (optional)",
                },
            },
            "required": ["project_path"],
        },
    }


def _analyze_image_schema() -> dict:
    return {
        "name": "analyze_image",
        "description": (
            "[Export] Analyze an image (diagram, screenshot, mockup) using local vision LLM. "
            "Runs fully offline. Requires: ollama pull granite3.2-vision:2b"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to image file (PNG, JPG, GIF, WebP, BMP)",
                },
            },
            "required": ["file_path"],
        },
    }


# ======================================================================
# Async wrappers for project tools
# ======================================================================


async def _git_history_async(
    adapter: IEditorAdapter, file_path: str, max_count: int
) -> dict:
    from src.mcp.tools_project import git_history_tool

    return await git_history_tool(adapter, file_path=file_path, max_count=max_count)


async def _git_diff_async(adapter: IEditorAdapter, file_path: str) -> dict:
    from src.mcp.tools_project import git_diff_tool

    return await git_diff_tool(adapter, file_path=file_path)


async def _git_branches_async(adapter: IEditorAdapter) -> dict:
    from src.mcp.tools_project import git_branches_tool

    return await git_branches_tool(adapter)


async def _get_file_tree_async(adapter: IEditorAdapter, path: str) -> dict:
    from src.mcp.tools_project import get_file_tree_tool

    return await get_file_tree_tool(adapter, path=path)


async def _generate_diagram_async(
    project_path: str, aspect: str, diagram_type: str, node_path: str, relation_scope: str = ""
) -> dict:
    from src.mcp.tools_project import generate_diagram_tool

    return await generate_diagram_tool(
        project_path=project_path,
        aspect_name=aspect,
        diagram_type=diagram_type,
        node_path=node_path,
        relation_scope=relation_scope,
    )


async def _list_diagram_types_async(aspect: str) -> dict:
    from src.mcp.tools_project import list_diagram_types_tool

    return await list_diagram_types_tool(aspect=aspect)


async def _generate_local_diagram_async(
    project_path: str, storage, aspect: str, node_path: str
) -> dict:
    from pathlib import Path

    from src.export.local_diagram import LocalDiagramGenerator

    gen = LocalDiagramGenerator(storage)
    return await gen.generate(
        project_path=Path(project_path),
        aspect=aspect,
        node_path=node_path,
    )


async def _analyze_image_async(file_path: str) -> dict:
    from src.export.image_analyzer import ImageAnalyzer

    analyzer = ImageAnalyzer(storage=None)
    return await analyzer.analyze(file_path)


# ======================================================================
# Stdio transport
# ======================================================================


def run_stdio_server(handler: MCPHandler) -> None:
    """Run MCP server over stdio (JSON-RPC via stdin/stdout)."""
    for line in sys.stdin:
        try:
            request = json.loads(line.strip())
        except json.JSONDecodeError:
            continue

        method = request.get("method", "")
        req_id = request.get("id", 0)
        params = request.get("params", {})

        if method == "shutdown":
            break

        # REQ-001: measure from transport layer entry to response emission
        start = time.perf_counter()
        result = handler.handle_request(method, params)
        _respond_stdio(req_id, result)
        if method == "tools/call":
            _log_perf(params.get("name", ""), (time.perf_counter() - start) * 1000)


def _respond_stdio(req_id, result: dict) -> None:
    sys.stdout.write(
        json.dumps(
            {"jsonrpc": "2.0", "id": req_id, "result": result}, ensure_ascii=False
        )
        + "\n"
    )
    sys.stdout.flush()


# ======================================================================
# HTTP transport
# ======================================================================


class _MCPHTTPHandler(BaseHTTPRequestHandler):
    """HTTP request handler for MCP JSON-RPC over POST /mcp."""

    mcp_handler: MCPHandler = None  # type: ignore[assignment]

    def log_message(self, format, *args):
        pass  # Suppress HTTP server logs to stderr

    def do_GET(self):
        """Serve SSE /events endpoint, MCP health check, or proxy to Planka."""
        if self.path == "/events":
            self._handle_sse()
        elif self.path == "/mcp":
            self._handle_mcp_get()
        else:
            self._handle_planka_proxy()

    def _handle_mcp_get(self):
        """Health check / SSE negotiation for MCP Streamable HTTP."""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b"ok")

    def _handle_sse(self):
        """Set up SSE stream for real-time push notifications."""
        handler = self.mcp_handler
        if handler is None:
            self.send_error(500, "MCP handler not initialized")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        import threading

        stop_event = threading.Event()

        def write_fn(data: bytes) -> None:
            try:
                self.wfile.write(data)
                self.wfile.flush()
            except (BrokenPipeError, OSError, ConnectionResetError):
                stop_event.set()

        from src.mcp.sse import stream_sse_events

        stream_sse_events(handler.sse_hub, write_fn, stop_event)

    def _handle_planka_proxy(self):
        """Proxy all non-MCP requests to Planka at localhost:3001 with auth token.

        If the path starts with /planka/, the prefix is stripped.
        Otherwise the path is forwarded as-is (for Planka's own API/Socket.IO calls).
        """
        import urllib.request
        import urllib.error

        path = self.path
        if path.startswith("/planka/"):
            path = path[7:]  # strip /planka prefix
            if not path:
                path = "/"

        # Preserve query string
        target = f"http://localhost:3001{path}"
        if "?" in self.path:
            target += "?" + self.path.split("?", 1)[1]

        try:
            req = urllib.request.Request(target)
            req.add_header("Authorization", f"Bearer {PLANKA_TOKEN}")
            # Forward ALL client headers to Planka (needed for Socket.IO handshake)
            for hdr_name, hdr_val in self.headers.items():
                low = hdr_name.lower()
                if low in ("host",):  # let urllib set the correct Host
                    continue
                if low.startswith("x-") or low in ("origin", "referer", "user-agent",
                                                     "accept", "accept-language",
                                                     "accept-encoding", "connection",
                                                     "cookie"):
                    req.add_header(hdr_name, hdr_val)

            try:
                resp = urllib.request.urlopen(req, timeout=30)
            except urllib.error.HTTPError as err:
                resp = err  # Forward non-2xx responses too (e.g. Socket.IO 400)

            body = resp.read()
            content_type = resp.getheader("Content-Type", "")

            # Inject access token cookie into Planka's HTML so the React
            # app finds it and considers the user logged in.
            if "text/html" in content_type and body:
                from email.utils import formatdate as _fmtdate
                import base64 as _b64
                try:
                    payload_b64 = PLANKA_TOKEN.split(".")[1]
                    payload_b64 += "=" * (4 - len(payload_b64) % 4)
                    decoded = json.loads(_b64.urlsafe_b64decode(payload_b64))
                    exp_ts = decoded.get("exp", 9999999999)
                    exp_date = _fmtdate(exp_ts, usegmt=True)
                except Exception:
                    exp_date = _fmtdate(9999999999, usegmt=True)

                cookie_js = (
                    b"<script>"
                    b"document.cookie='accessToken=" + PLANKA_TOKEN.encode() + b";path=/;expires=" + exp_date.encode() + b";SameSite=Strict';"
                    b"document.cookie='accessTokenVersion=1;path=/;expires=" + exp_date.encode() + b";SameSite=Strict';"
                    b"window.__SOCKET_IO_OPTS__={transports:['polling']};"
                    b"</script>"
                )
                body = body.replace(b"</head>", cookie_js + b"</head>")

            self.send_response(resp.status)
            for key, val in resp.getheaders():
                low = key.lower()
                if low not in ("transfer-encoding", "content-length"):
                    self.send_header(key, val)
            # Content-Length may have changed due to token injection
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            self.send_error(502, f"Planka proxy error: {exc}")

    def _handle_planka_proxy_post(self):
        """Proxy POST/PUT/PATCH/DELETE requests to Planka."""
        import urllib.request
        import urllib.error

        path = self.path
        if path.startswith("/planka/"):
            path = path[7:]
            if not path:
                path = "/"

        target = f"http://localhost:3001{path}"
        if "?" in self.path:
            target += "?" + self.path.split("?", 1)[1]

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        try:
            req = urllib.request.Request(target, data=body, method=self.command)
            req.add_header("Authorization", f"Bearer {PLANKA_TOKEN}")
            # Forward relevant headers
            for hdr in ("content-type", "accept", "accept-language", "origin"):
                if hdr in self.headers:
                    req.add_header(hdr.title(), self.headers[hdr])
            if "cookie" in self.headers:
                req.add_header("Cookie", self.headers["cookie"])

            try:
                resp = urllib.request.urlopen(req, timeout=30)
            except urllib.error.HTTPError as err:
                resp = err

            resp_body = resp.read()
            self.send_response(resp.status)
            for key, val in resp.getheaders():
                low = key.lower()
                if low not in ("transfer-encoding",):
                    self.send_header(key, val)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(resp_body)
        except Exception as exc:
            self.send_error(502, f"Planka proxy {self.command} error: {exc}")

    def do_POST(self):
        """Handle MCP JSON-RPC or proxy to Planka."""
        if self.path != "/mcp":
            self._handle_planka_proxy_post()
            return

        # REQ-001: measure from transport layer entry to response emission
        start = time.perf_counter()
        content_length = int(self.headers.get("Content-Length", 0))

        try:
            body = self.rfile.read(content_length)
        except (ConnectionResetError, ConnectionAbortedError, OSError) as exc:
            # Client disconnected before sending full body — ignore silently
            logger.debug("mcp_client_disconnected", error=str(exc))
            return

        try:
            request = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        method = request.get("method", "")
        req_id = request.get("id", 0)
        params = request.get("params", {})

        if method == "shutdown":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"jsonrpc":"2.0","id":0,"result":"ok"}')
            return

        result = self.mcp_handler.handle_request(method, params)
        response = {"jsonrpc": "2.0", "id": req_id, "result": result}

        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            # Client disconnected during response — response is already computed
            pass

        if method == "tools/call":
            _log_perf(params.get("name", ""), (time.perf_counter() - start) * 1000)

    def do_OPTIONS(self):
        """CORS preflight for MCP and Planka proxy."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, Accept")
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.end_headers()

    def do_PUT(self):
        self._handle_planka_proxy_post()

    def do_PATCH(self):
        self._handle_planka_proxy_post()

    def do_DELETE(self):
        self._handle_planka_proxy_post()


_LOCALHOST_ONLY = "127.0.0.1"


@implements("REQ-002")
def run_http_server(handler: MCPHandler, host: str, port: int) -> None:
    """Run MCP server over HTTP POST /mcp.

    REQ-002: Defaults to 127.0.0.1 (localhost) for security.
    When ``host`` is explicitly set to ``0.0.0.0`` (e.g. Docker),
    respects the argument to allow container port forwarding.
    """
    bind_host = host if host == "0.0.0.0" else _LOCALHOST_ONLY
    _MCPHTTPHandler.mcp_handler = handler  # type: ignore[assignment]
    server = ThreadingHTTPServer((bind_host, port), _MCPHTTPHandler)
    print(
        f"[spec-editor] MCP HTTP server listening on http://{bind_host}:{port}/mcp",
        file=sys.stderr,
    )
    print(f"[spec-editor] Read-only: {not handler._writable}", file=sys.stderr)
    print(
        "[spec-editor] Waiting for agent connection... (Ctrl+C to stop)",
        file=sys.stderr,
    )
    server.serve_forever()


# ======================================================================
# Auto-restart watchdog (REQ-004)
# ======================================================================

_MAX_RESTART_ATTEMPTS = 3
_RESTART_DELAY_SECONDS = 2


@implements("REQ-004")
def run_with_auto_restart(fn) -> None:
    """Wrap a callable with auto-restart on crash (REQ-004).

    Up to _MAX_RESTART_ATTEMPTS retries with _RESTART_DELAY_SECONDS delay
    between attempts. KeyboardInterrupt (Ctrl+C) is propagated immediately.
    """
    import time

    for attempt in range(1, _MAX_RESTART_ATTEMPTS + 1):
        try:
            fn()
            return  # Clean exit, no restart needed
        except KeyboardInterrupt:
            print("\n[spec-editor] Shutting down...", file=sys.stderr)
            return
        except Exception as exc:
            if attempt < _MAX_RESTART_ATTEMPTS:
                print(
                    f"[spec-editor] Server crashed ({exc}). "
                    f"Restarting in {_RESTART_DELAY_SECONDS}s "
                    f"(attempt {attempt}/{_MAX_RESTART_ATTEMPTS})...",
                    file=sys.stderr,
                )
                time.sleep(_RESTART_DELAY_SECONDS)
            else:
                print(
                    f"[spec-editor] Server crashed ({exc}). "
                    f"Max retries ({_MAX_RESTART_ATTEMPTS}) exceeded. Giving up.",
                    file=sys.stderr,
                )
                raise


# ======================================================================
# Entry point
# ======================================================================


@implements("REQ-002")
def mcp_server(
    path: str | None = None,
    transport: str = "stdio",
    port: int = _DEFAULT_MCP_PORT,
    read_only: bool = False,
    host: str = "127.0.0.1",
    socket: str = "",
    adapter: IEditorAdapter | None = None,
) -> None:
    """Start MCP server with specified transport and mode.

    Args:
        path: Path to spec-editor project directory
        transport: "stdio" (default) or "http"
        port: HTTP port (default 8088)
        read_only: If True, only read-only tools are registered (HTTP only)
        host: Ignored for HTTP transport (always binds to 127.0.0.1 per REQ-002)
        adapter: EditorAdapter instance (default: StandaloneAdapter)
    """
    project_path = Path(path).resolve() if path else None

    # For stdio, always full access (local, no security risk)
    writable = True if transport == "stdio" else not read_only

    handler = MCPHandler(project_path=project_path, writable=writable, adapter=adapter)

    if transport == "http":
        run_with_auto_restart(lambda: run_http_server(handler, host, port))
    elif transport == "socket":
        run_with_auto_restart(lambda: _run_socket_server(handler, socket))
    else:
        run_with_auto_restart(lambda: run_stdio_server(handler))


def _run_socket_server(handler, socket_path: str) -> None:
    """Run MCP server on a Unix domain socket."""
    import asyncio
    import os as _os

    if not socket_path:
        raise ValueError("socket path required for socket transport")

    # Remove stale socket
    if _os.path.exists(socket_path):
        _os.unlink(socket_path)

    async def _handle_client(reader, writer):
        # REQ-001: measure from transport layer entry to response emission
        start = time.perf_counter()
        data = await reader.read(65536)
        if not data:
            writer.close()
            return
        try:
            request = json.loads(data.decode())
            method = request.get("method", "")
            params = request.get("params", {})
            response = handler.handle_request(request)
            writer.write(json.dumps(response).encode() + b"\n")
        except Exception as exc:
            method = ""
            params = {}
            writer.write(
                json.dumps(
                    {"jsonrpc": "2.0", "error": {"code": -32603, "message": str(exc)}}
                ).encode()
                + b"\n"
            )
        await writer.drain()
        writer.close()
        if method == "tools/call":
            _log_perf(params.get("name", ""), (time.perf_counter() - start) * 1000)

    async def _serve():
        server = await asyncio.start_unix_server(_handle_client, path=socket_path)
        print(f"MCP socket: {socket_path}", flush=True)
        async with server:
            await server.serve_forever()

    asyncio.run(_serve())


# ======================================================================
# Helpers
# ======================================================================


def _fake_methodology():
    from src.config.methodology import Methodology

    return Methodology(name="mcp", version="1.0")


def _is_run_active_schema() -> dict:
    return {
        "name": "is_run_active",
        "description": (
            "[Project] Check if spec-editor run is currently executing in the project. "
            "Returns active status and PID of the running process. Requires project_path."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **_PROJECT_PATH_SCHEMA,
            },
            "required": ["project_path"],
        },
    }


def _search_semantic_schema() -> dict:
    from src.mcp.tools_semantic import search_semantic_schema
    return search_semantic_schema()

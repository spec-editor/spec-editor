"""MCP tool: search_semantic — semantic code search via embeddings.

Registers with the MCP server to provide AI agents with codebase
understanding beyond exact symbol name matching.

First-time indexing runs in the background: the tool waits up to 1 second,
and if the index isn't ready yet, returns ``status="indexing"`` so the
caller can retry shortly (instead of blocking for tens of seconds).

Usage via MCP:
    search_semantic(query="payment processing", top_k=10)
    search_semantic(query="error handling middleware")
"""

from __future__ import annotations

import multiprocessing
import threading
from pathlib import Path

from src.code_index.index import EmbeddingIndex

# Background index builds, keyed by project path.
# First call starts a subprocess and returns "indexing" after a 1s grace period.
_BUILDS: dict[str, dict] = {}
_BUILDS_LOCK = threading.Lock()

# How long to wait for a first-time index before reporting "indexing".
_INDEX_WAIT_SECONDS = 1.0


def _build_index_process(project_path: str, mode: str) -> None:
    """Build the semantic index in a CHILD PROCESS (top-level, picklable).

    tree-sitter Parser objects are thread-confined — they cannot be created,
    used, or dropped across threads (cross-thread use raises PanicException,
    cross-thread drop raises "Parser is unsendable"). Running the build in a
    separate process keeps all parsers on the child's main thread.

    mode: "full" — complete rebuild; "incremental" — re-index only changed files.
    """
    idx = EmbeddingIndex(Path(project_path))
    if mode == "incremental" and idx.is_ready():
        idx.rebuild_incremental()
    else:
        idx.build(force=(mode == "full"))


def search_semantic_tool(
    project_path: str,
    query: str = "",
    top_k: int = 10,
    rebuild: bool = False,
) -> dict:
    """Semantic code search using embeddings.

    Args:
        project_path: Path to spec-editor project directory.
        query: Natural language search query.
        top_k: Number of results to return (default 10, max 50).
        rebuild: Force rebuild the index before searching (synchronous).

    Returns:
        dict with "status" ("ok" | "indexing" | "error"), "results", and
        "chunks_total" (when ready). If the index is stale (source changed),
        a background refresh is started and results are served from the
        current (slightly outdated) index.
    """
    if not query or not query.strip():
        return {"status": "error", "error": "query is required", "results": []}

    pp = Path(project_path)
    if not pp.is_dir():
        return {
            "status": "error",
            "error": f"Project directory not found: {project_path}",
            "results": [],
        }

    top_k = min(max(1, top_k), 50)

    try:
        index = EmbeddingIndex(pp)

        if rebuild:
            # Explicit rebuild — synchronous (caller opted into the wait).
            index.build(force=True)
            return _search(index, query, top_k)

        if not index.is_ready():
            # First-time indexing — background subprocess + grace period.
            entry = _start_background_build(pp, mode="full")
            entry["process"].join(timeout=_INDEX_WAIT_SECONDS)
            if not EmbeddingIndex(pp).is_ready():
                return {
                    "status": "indexing",
                    "query": query,
                    "results": [],
                    "message": (
                        "Semantic index is being built (first-time indexing). "
                        "This typically takes 10-60 seconds. Retry the query shortly."
                    ),
                }
            index = EmbeddingIndex(pp)  # reload freshly-built index

        elif index.is_stale():
            # Source changed since last build — incremental refresh in the
            # background while serving results from the current index.
            _start_background_build(pp, mode="incremental")

        return _search(index, query, top_k)
    except Exception as e:
        return {"status": "error", "error": str(e), "results": []}


def _search(index: EmbeddingIndex, query: str, top_k: int) -> dict:
    """Run the search and format the 'ok' response."""
    results = index.search(query, top_k=top_k, min_score=0.15)
    return {
        "status": "ok",
        "query": query,
        "results": results,
        "chunks_total": len(index._chunks),
    }


def _start_background_build(pp: Path, mode: str) -> dict:
    """Start (or reuse) a background index build in a subprocess.

    The build runs in a separate process so tree-sitter parsers are created,
    used, and dropped on a single thread (the child's main thread).
    """
    key = str(pp)

    with _BUILDS_LOCK:
        entry = _BUILDS.get(key)
        if entry is None or not entry["process"].is_alive():
            proc = multiprocessing.Process(
                target=_build_index_process,
                args=(str(pp), mode),
                daemon=True,
            )
            proc.start()
            entry = {"process": proc}
            _BUILDS[key] = entry

    return entry


def search_semantic_schema() -> dict:
    """Return MCP tool schema for tools/list."""
    return {
        "name": "search_semantic",
        "description": (
            "Semantic (natural language) search across the codebase. "
            "Returns matching functions, classes, and methods with relevance scores. "
            "Unlike search_symbol (exact name match), this finds code by meaning "
            "(e.g., 'payment processing' finds process_payment even if the word "
            "'payment' isn't in the function name). "
            "Uses a local ONNX embedding model (all-MiniLM-L6-v2) via fastembed — "
            "no external service required. First call builds the index and "
            "downloads the model (typically 10-60 sec for medium projects); "
            "subsequent queries are milliseconds."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language query describing what to find",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results (default 10, max 50)",
                },
                "rebuild": {
                    "type": "boolean",
                    "description": "Force rebuild the index (default false)",
                },
            },
            "required": ["query"],
        },
    }

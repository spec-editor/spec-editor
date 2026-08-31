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

import threading
from pathlib import Path

from src.code_index.index import EmbeddingIndex

# Background index builds, keyed by project path.
# First call starts a thread and returns "indexing" after a 1s grace period.
_BUILDS: dict[str, dict] = {}
_BUILDS_LOCK = threading.Lock()

# How long to wait for a first-time index before reporting "indexing".
_INDEX_WAIT_SECONDS = 1.0


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
        "chunks_total" (when ready).
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
        elif not index.is_ready():
            ready = _ensure_index_ready(index, pp)
            if not ready:
                return {
                    "status": "indexing",
                    "query": query,
                    "results": [],
                    "message": (
                        "Semantic index is being built (first-time indexing). "
                        "This typically takes 10-60 seconds. Retry the query shortly."
                    ),
                }

        results = index.search(query, top_k=top_k, min_score=0.15)

        return {
            "status": "ok",
            "query": query,
            "results": results,
            "chunks_total": len(index._chunks),
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "results": []}


def _ensure_index_ready(index: EmbeddingIndex, pp: Path) -> bool:
    """Start a background index build if needed and wait up to 1 second.

    Returns True if the index became ready within the grace period,
    False if indexing is still in progress.
    """
    key = str(pp)

    with _BUILDS_LOCK:
        entry = _BUILDS.get(key)
        # Start a new build only if none is running AND the index is not ready.
        if entry is None or entry["thread"].is_alive() is False:
            if not index.is_ready():
                state: dict = {"done": False, "chunks": 0}

                def _run() -> None:
                    try:
                        state["chunks"] = index.build(force=False)
                    except Exception:
                        pass
                    finally:
                        state["done"] = True

                t = threading.Thread(target=_run, daemon=True)
                t.start()
                entry = {"thread": t, "state": state}
                _BUILDS[key] = entry

        if entry is None:
            return index.is_ready()
        thread = entry["thread"]

    # Wait up to 1s for the build to finish.
    thread.join(timeout=_INDEX_WAIT_SECONDS)
    return index.is_ready()


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

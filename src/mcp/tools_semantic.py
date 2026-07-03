"""MCP tool: search_semantic — semantic code search via embeddings.

Registers with the MCP server to provide AI agents with codebase
understanding beyond exact symbol name matching.

Usage via MCP:
    search_semantic(query="payment processing", top_k=10)
    search_semantic(query="error handling middleware")
"""

from __future__ import annotations

from pathlib import Path

from src.code_index.index import EmbeddingIndex


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
        rebuild: Force rebuild the index before searching.

    Returns:
        dict with "results" list and "chunks_total" count.
    """
    if not query or not query.strip():
        return {"error": "query is required", "results": []}

    pp = Path(project_path)
    if not pp.is_dir():
        return {"error": f"Project directory not found: {project_path}", "results": []}

    top_k = min(max(1, top_k), 50)

    try:
        index = EmbeddingIndex(pp)

        if rebuild or not index._chunks_path.exists():
            index.build(force=rebuild)

        results = index.search(query, top_k=top_k, min_score=0.15)

        return {
            "query": query,
            "results": results,
            "chunks_total": len(index._chunks) if index._chunks_path.exists() else 0,
        }
    except Exception as e:
        return {"error": str(e), "results": []}


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
            "Uses local embedding model (all-MiniLM-L6-v2) via Ollama. "
            "First call builds the index (~1-10 sec for medium projects)."
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

"""Agent tools for working with code: read source/, search, verification."""

import asyncio
import os
import threading
import time as _time_module
from pathlib import Path
from typing import Callable

from src.config import get_logger
from src.config.methodology import Methodology
from src.providers.base import ToolDef, make_tool_params as _params
from src.storage.adapter import StorageAdapter

logger = get_logger(__name__)

# ======================================================================
# Symbol cache for search_symbol_tool (lazy mtime invalidation)
# ======================================================================

# {abs_file_path: (mtime_float, [symbol_dict, ...])}
_symbol_cache: dict[str, tuple[float, list[dict]]] = {}
# Resolved code_dir string — cleared when code_dir changes
_cache_code_dir: str = ""
# Cached parser mapping (lazy loaded once per code_dir)
_cache_parsers: dict[str, tuple[str, Callable]] = {}
# Timestamp of last full rescan (for finding new files)
_last_full_scan: float = 0.0
# Seconds between periodic full rescans
_FULL_SCAN_INTERVAL: float = 30.0
# Thread safety for concurrent MCP requests
_cache_lock: threading.Lock = threading.Lock()


def _clear_symbol_cache() -> None:
    """Clear all cached symbol data. Exposed for testing."""
    global _symbol_cache, _cache_code_dir, _cache_parsers, _last_full_scan
    _symbol_cache.clear()
    _cache_code_dir = ""
    _cache_parsers.clear()
    _last_full_scan = 0.0


def _load_parsers() -> None:
    """Lazy-load language parsers into _cache_parsers (module-level)."""
    global _cache_parsers
    if _cache_parsers:
        return
    try:
        from src.mcp.parsers.python import parse_python
        _cache_parsers[".py"] = ("python", parse_python)
    except Exception:
        pass
    try:
        from src.mcp.parsers.typescript import parse_typescript
        _cache_parsers[".ts"] = ("typescript", parse_typescript)
        _cache_parsers[".tsx"] = ("typescript", parse_typescript)
        _cache_parsers[".js"] = ("typescript", parse_typescript)
    except Exception:
        pass
    try:
        from src.mcp.parsers.go import parse_go
        _cache_parsers[".go"] = ("go", parse_go)
    except Exception:
        pass
    try:
        from src.mcp.parsers.java import parse_java
        _cache_parsers[".java"] = ("java", parse_java)
    except Exception:
        pass
    try:
        from src.mcp.parsers.rust import parse_rust
        _cache_parsers[".rs"] = ("rust", parse_rust)
    except Exception:
        pass


def _symbols_to_dicts(
    symbols: list, file_path: Path, root: Path, language: str
) -> list[dict]:
    """Convert CodeSymbol objects to the dicts returned by search_symbol_tool."""
    result: list[dict] = []
    for sym in symbols:
        result.append({
            "name": sym.name,
            "kind": sym.kind,
            "file": str(file_path.relative_to(root)),
            "line": sym.line,
            "language": language,
            "decorators": getattr(sym, "decorators", [])[:5],
            "docstring": (getattr(sym, "docstring", "") or "")[:200],
        })
    return result


def _cache_cleanup(root: Path) -> int:
    """Remove cache entries for files that no longer exist. Returns removed count."""
    removed = 0
    for abs_path in list(_symbol_cache.keys()):
        if not os.path.isfile(abs_path):
            _symbol_cache.pop(abs_path, None)
            removed += 1
    return removed

# ======================================================================
# Tool functions
# ======================================================================


async def read_source_document(source_dir, filename: str | None = None) -> dict:
    """Read raw input documents from source/ folder (NOT specification elements).
    These are unprocessed inputs — chats, PDFs, text files. Use read_element
    to read specification elements. Without arguments — file listing."""
    import os

    sp = source_dir
    if not os.path.isdir(sp):
        return {"error": "source/ folder not found"}

    if filename:
        fp = os.path.join(sp, filename)
        if not os.path.isfile(fp):
            return {
                "error": f"File '{filename}' not found in source/",
                "files": os.listdir(sp),
            }
        with open(fp, encoding="utf-8") as f:
            return {"filename": filename, "content": f.read()}
    else:
        files = sorted([f for f in os.listdir(sp) if f.endswith((".md", ".txt"))])
        return {"files": files, "count": len(files)}


async def convert_source_file(file_path: str) -> dict:
    """Convert a file (PDF, HTML, DOCX) to Markdown text.

    Uses SourcePreprocessor.read_file() from the ingestion pipeline.
    """
    from src.ingestion.preprocessor import SourcePreprocessor

    try:
        text = SourcePreprocessor.read_file(Path(file_path))
        return {"status": "ok", "content": text, "file_path": file_path}
    except Exception as e:
        return {"status": "error", "message": str(e), "file_path": file_path}


async def compact_context_tool(agent=None, reason: str = "") -> dict:
    """Compact the agent's context. Agent calls when it feels the context is overloaded."""
    if agent and hasattr(agent, "compact_now"):
        agent.compact_now(reason or "manual call")
        return {
            "status": "ok",
            "message": "Context will be compacted on the next LLM call",
        }
    return {"status": "error", "message": "Compaction is unavailable"}


async def export_srs_tool(
    storage: StorageAdapter, project_path: str, template_path: str
) -> dict:
    """Export the specification to an SRS document."""
    from src.export.pipeline import pipeline_from_config

    pipeline = pipeline_from_config(
        {"gatherer": "srs", "formatter": "markdown", "transport": "file"},
        storage,
        Path(project_path),
    )
    _, data = pipeline.run(
        storage,
        Path(template_path),
        Path(project_path),
        transport_config={"output": str(Path(project_path) / "srs.md")},
    )
    total = sum(len(s.elements) for s in data.sections)
    return {
        "status": "ok",
        "sections": len(data.sections),
        "elements": total,
        "duplicates_found": data.metadata.get("duplicates", 0),
        "content": "\n\n".join(
            f"## {s.title}\n"
            + "\n".join(
                f"**{e.id}** — {e.title}\n{e.content[:500]}" for e in s.elements[:20]
            )
            for s in data.sections
        ),
    }


async def request_helper(spawner, role: str, task: str) -> dict:
    """Request a helper agent for parallel work on a specific task.

    Args:
        role: helper role (e.g., "modules", "UI", "scenarios")
        task: specific assignment (e.g., "elaborate module architecture")
    """
    if spawner is None:
        return {"error": "Helpers are not supported in this mode"}
    try:
        helper_name = await spawner(role, task)
        return {"status": "ok", "helper": helper_name, "role": role, "task": task}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


async def search_code_tool(code_dir: str, pattern: str) -> dict:
    """Search through code (grep)."""
    import subprocess
    from pathlib import Path

    d = Path(code_dir)
    if not d.is_dir():
        return {"error": f"Directory not found: {code_dir}"}
    try:
        result = subprocess.run(
            ["grep", "-rn", "--include=*.py", pattern, str(d)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        lines = result.stdout.strip().split("\n")[:30]
        return {"matches": len(lines), "lines": lines}
    except Exception as exc:
        return {"error": str(exc)}


async def get_file_tree_tool(code_dir: str) -> dict:
    """Show the project file tree (code + spec + config files)."""
    from pathlib import Path

    d = Path(code_dir)
    if not d.is_dir():
        return {"error": f"Directory not found: {code_dir}"}

    # Build a readable tree
    tree_lines = []
    _walk(d, tree_lines, prefix="", max_depth=4, max_items=200)
    return {"root": str(d), "tree": "\n".join(tree_lines)}


def _walk(
    path: "Path",
    lines: list,
    prefix: str = "",
    max_depth: int = 3,
    max_items: int = 200,
) -> None:
    """Recursively build a tree representation."""
    from pathlib import Path as _Path

    if max_depth <= 0 or len(lines) >= max_items:
        return

    try:
        entries = sorted(
            [e for e in path.iterdir() if not e.name.startswith(".")],
            key=lambda e: (not e.is_dir(), e.name),
        )
    except PermissionError:
        return

    for i, entry in enumerate(entries):
        if len(lines) >= max_items:
            lines.append(f"{prefix}... ({max_items}+ items)")
            return
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        if entry.is_dir():
            lines.append(f"{prefix}{connector}{entry.name}/")
            _walk(
                entry,
                lines,
                prefix + ("    " if is_last else "│   "),
                max_depth - 1,
                max_items,
            )
        else:
            size = entry.stat().st_size
            lines.append(f"{prefix}{connector}{entry.name} ({_fmt_size(size)})")


def _fmt_size(size: int) -> str:
    if size >= 1_000_000:
        return f"{size / 1_000_000:.1f}MB"
    if size >= 1_000:
        return f"{size / 1_000:.1f}KB"
    return f"{size}B"


async def read_lints_tool(code_dir: str, file_path: str | None = None) -> dict:
    """Check code for errors (ruff/pyright)."""
    import subprocess
    from pathlib import Path

    target = str(Path(code_dir) / file_path) if file_path else code_dir
    try:
        result = subprocess.run(
            ["ruff", "check", target],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=code_dir,
        )
        lines = result.stdout.strip().split("\n")[:20]
        return {"errors": len(lines), "lines": lines}
    except FileNotFoundError:
        return {"error": "ruff is not installed"}
    except Exception as exc:
        return {"error": str(exc)}


async def verify_implements_tool(
    storage: StorageAdapter,
    code_dir: str,
    file_path: str,
) -> dict:
    """Verify that a code file implements requirements (@implements).

    Also writes bidirectional traceability links:
    - IMP-* → spec elements (implements)
    - spec elements → IMP-* (implemented_by)

    Args:
        code_dir: path to the code directory
        file_path: relative path to the file inside code_dir
    """
    from pathlib import Path

    from src.mcp.verifier import verify_implements

    full_path = Path(code_dir) / file_path
    report = verify_implements(storage, full_path, write_back=True)
    return {
        "passed": report.passed,
        "implemented": report.implemented,
        "links_synced": report.links_synced,
        "gaps": [
            {
                "req_id": g.req_id,
                "file_path": g.file_path,
                "severity": g.severity,
                "message": g.message,
            }
            for g in report.gaps
        ],
    }


async def verify_traceability_tool(
    storage: StorageAdapter,
    code_dir: str,
    language: str = "python",
    write_back: bool = False,
) -> dict:
    """Check requirement coverage by code across the entire project.

    If write_back=True, also creates/updates IMP-* elements and
    writes bidirectional traceability links.

    Returns coverage, list of implemented requirements and gaps.
    """
    from pathlib import Path

    from src.mcp.verifier import verify_traceability

    report = verify_traceability(storage, Path(code_dir), language, write_back=write_back)
    return {
        "passed": report.passed,
        "total_requirements": report.total_requirements,
        "implemented": report.implemented,
        "coverage": report.coverage,
        "links_synced": report.links_synced,
        "gaps": [
            {
                "req_id": g.req_id,
                "file_path": g.file_path,
                "severity": g.severity,
                "message": g.message,
            }
            for g in report.gaps[:50]
        ],
        "gaps_count": len(report.gaps),
    }


async def annotate_code_tool(
    storage: StorageAdapter,
    code_dir: str,
    dry_run: bool = True,
) -> dict:
    """Annotate existing code with @implements annotations based on names.

    Default dry_run=True — shows what would be changed without writing.
    dry_run=False — actually modifies files.
    """
    from pathlib import Path

    from src.mcp.annotator import annotate_code

    result = annotate_code(storage, Path(code_dir), dry_run=dry_run)
    return result


async def _parse_file_safe(
    file_path: Path,
    parse_fn: Callable,
    root: Path,
    lang_name: str,
) -> tuple[str, list[dict]] | None:
    """Parse a single file in a thread pool. Returns (abs_path, symbol_dicts) or None.

    Uses asyncio.to_thread() so the event loop is never blocked.
    Each file is parsed in its own thread — parser creation and destruction
    happen on the same thread (safe for tree-sitter).
    """
    abs_path = str(file_path)

    def _parse():
        _, symbols = parse_fn(file_path)
        return _symbols_to_dicts(symbols, file_path, root, lang_name)

    try:
        symbol_dicts = await asyncio.to_thread(_parse)
        return (abs_path, symbol_dicts)
    except Exception as exc:
        logger.warning(
            "search_symbol: cannot parse file, skipping",
            path=abs_path,
            error=str(exc),
        )
        return None


async def search_symbol_tool(code_dir: str, query: str) -> dict:
    """Find CLASS/FUNCTION/METHOD DEFINITIONS by name using AST parsers.

    The primary tool for symbol lookup. Uses language-specific parsers
    (Python AST, TypeScript tree-sitter, Go, Java, Rust) — NOT grep.
    Returns structured data per symbol: name, kind (class/function/method),
    file path, line number, decorators, and docstring (first 200 chars).

    Uses lazy mtime-based caching: on first call parses all files; on subsequent
    calls only re-parses files whose mtime has changed. A periodic full rescan
    (every 30s) picks up newly created files.

    File parsing runs in parallel via asyncio.to_thread() — one thread per file,
    so multi-core machines see significant speedup on first scan.

    Use this instead of grep/search_code when the user asks:
    - "Find class X" / "Where is class X defined?"
    - "Find function Y" / "Show me the definition of Y"
    - "What methods does class Z have?"
    - Any query about code symbols by name
    """
    global _symbol_cache, _cache_code_dir, _cache_parsers, _last_full_scan

    d = Path(code_dir).resolve()
    if not d.is_dir():
        return {"error": f"Directory not found: {code_dir}", "symbols": []}

    # ── Detect code_dir change → reset cache ──
    code_dir_str = str(d)
    if _cache_code_dir != code_dir_str:
        with _cache_lock:
            _clear_symbol_cache()
            _cache_code_dir = code_dir_str

    # ── Lazy load parsers (cached globally) ──
    _load_parsers()
    parsers = _cache_parsers

    skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "dist", "build",
                 ".next", ".vscode-test", "egg-info", "out"}

    now = _time_module.time()
    need_full_scan = (now - _last_full_scan) > _FULL_SCAN_INTERVAL
    files_reparsed = 0

    if need_full_scan:
        # ═══════════════════════════════════════════════════════════════
        # Full scan: walk tree (under lock) → parse concurrently → update cache
        # ═══════════════════════════════════════════════════════════════

        # Phase 1: identify files to parse (under lock, fast — only stat calls)
        to_parse: list[tuple[Path, Callable, str]] = []  # (file_path, parse_fn, lang_name)

        with _cache_lock:
            _last_full_scan = now

            # os.walk with dir pruning — 60× faster than rglob("*") because
            # it never descends into .git, .venv, node_modules etc.
            parser_exts = set(parsers.keys())
            for dirpath, dirnames, filenames in os.walk(str(d)):
                # Prune ignored directories BEFORE descending
                dirnames[:] = [dn for dn in dirnames if dn not in skip_dirs]
                for fn in filenames:
                    suffix = os.path.splitext(fn)[1]
                    if suffix not in parser_exts:
                        continue
                    file_path = Path(dirpath) / fn
                    abs_path = str(file_path)
                    try:
                        current_mtime = file_path.stat().st_mtime
                    except OSError as exc:
                        logger.warning(
                            "search_symbol: cannot stat file, skipping",
                            path=abs_path,
                            error=str(exc),
                        )
                        continue

                    # Use cached symbols if mtime unchanged
                    if abs_path in _symbol_cache:
                        cached_mtime, _ = _symbol_cache[abs_path]
                        if current_mtime == cached_mtime:
                            continue

                    # Mark for parsing
                    _lang_name, parse_fn = parsers[suffix]
                    to_parse.append((file_path, parse_fn, _lang_name))

            # Remove entries for deleted files
            _cache_cleanup(d)

        # Phase 2: parse files in parallel via thread pool
        if to_parse:
            tasks = [
                _parse_file_safe(fp, pfn, d, ln)
                for fp, pfn, ln in to_parse
            ]
            parsed_results = await asyncio.gather(*tasks)

            # Phase 3: update cache under lock (re-stat to get post-parse mtime)
            with _cache_lock:
                for result in parsed_results:
                    if result is None:
                        continue
                    abs_path, symbol_dicts = result
                    try:
                        current_mtime = os.stat(abs_path).st_mtime
                    except OSError:
                        continue  # file disappeared, don't cache
                    _symbol_cache[abs_path] = (current_mtime, symbol_dicts)
                    files_reparsed += 1

    else:
        # ═══════════════════════════════════════════════════════════════
        # Incremental: stat cached files, reparse changed ones concurrently
        # ═══════════════════════════════════════════════════════════════

        changed_files: list[tuple[Path, Callable, str]] = []

        with _cache_lock:
            for abs_path in list(_symbol_cache.keys()):
                try:
                    current_mtime = os.stat(abs_path).st_mtime
                except OSError as exc:
                    logger.warning(
                        "search_symbol: cached file inaccessible, removing from cache",
                        path=abs_path,
                        error=str(exc),
                    )
                    _symbol_cache.pop(abs_path, None)
                    continue

                cached_mtime, _ = _symbol_cache[abs_path]
                if current_mtime != cached_mtime:
                    file_path = Path(abs_path)
                    suffix = file_path.suffix
                    if suffix in parsers:
                        _lang_name, parse_fn = parsers[suffix]
                        changed_files.append((file_path, parse_fn, _lang_name))

        if changed_files:
            tasks = [
                _parse_file_safe(fp, pfn, d, ln)
                for fp, pfn, ln in changed_files
            ]
            parsed_results = await asyncio.gather(*tasks)

            with _cache_lock:
                for result in parsed_results:
                    if result is None:
                        continue
                    abs_path, symbol_dicts = result
                    try:
                        current_mtime = os.stat(abs_path).st_mtime
                    except OSError:
                        _symbol_cache.pop(abs_path, None)
                        continue
                    _symbol_cache[abs_path] = (current_mtime, symbol_dicts)
                    files_reparsed += 1

    # ── Collect results from cache (under lock for read consistency) ──
    with _cache_lock:
        results: list[dict] = []
        q_lower = query.lower()
        for _abs_path, (_mtime, symbol_dicts) in _symbol_cache.items():
            for sym in symbol_dicts:
                if q_lower in sym["name"].lower():
                    results.append(sym)
                    if len(results) >= 50:
                        break
            if len(results) >= 50:
                break

        return {
            "query": query,
            "found": len(results),
            "files_scanned": len(_symbol_cache),
            "files_reparsed": files_reparsed,
            "symbols": sorted(results, key=lambda s: s["name"]),
        }


# ======================================================================
# ToolDef entries
# ======================================================================




CODE_RO_TOOLS: list[ToolDef] = [
    ToolDef(
        name="request_helper",
        description="[Agent] Request a helper agent for parallel work. role — role (modules/UI/scenarios/data/NFR), task — specific assignment.",
        parameters=_params(
            {
                "role": {
                    "type": "string",
                    "description": "Helper role (modules, UI, scenarios, data, NFR)",
                },
                "task": {
                    "type": "string",
                    "description": "Specific task for the helper",
                },
            },
            ["role", "task"],
        ),
    ),
    ToolDef(
        name="read_source_document",
        description="[Export] Read RAW input documents from source/ folder (chats, PDFs, text files). These are NOT specification elements — use read_element for those. No args — list files. With filename — file contents.",
        parameters=_params(
            {"filename": {"type": "string", "description": "Filename (optional)"}}
        ),
    ),
    ToolDef(
        name="convert_source_file",
        description="[Ingestion] Convert a file (PDF, HTML) to Markdown text. Returns the extracted content.",
        parameters=_params(
            {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file",
                }
            },
            ["file_path"],
        ),
    ),
    ToolDef(
        name="search_code",
        description="[Code] Grep for TEXT PATTERNS in source files (regex, strings, comments). For finding CLASS/FUNCTION/METHOD definitions by name, prefer search_symbol instead — it returns structured symbol data (name, kind, file, line, docstring).",
        parameters=_params(
            {
                "code_dir": {"type": "string", "description": "Path to code directory"},
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern for search",
                },
            },
            ["code_dir", "pattern"],
        ),
    ),
    ToolDef(
        name="get_file_tree",
        description="[Code] Show project file structure.",
        parameters=_params(
            {
                "code_dir": {"type": "string", "description": "Path to code directory"},
            },
            ["code_dir"],
        ),
    ),
    ToolDef(
        name="read_lints",
        description="[Code] Check code for errors via ruff.",
        parameters=_params(
            {
                "code_dir": {"type": "string", "description": "Path to code directory"},
                "file_path": {
                    "type": "string",
                    "description": "Specific file (optional)",
                },
            },
            ["code_dir"],
        ),
    ),
    ToolDef(
        name="export_srs",
        description="[Export] Export specification to SRS format (IEEE 830). Renders all elements with their relationships and content.",
        parameters=_params({}),
    ),
    ToolDef(
        name="compact_context",
        description="[Agent] Compact the agent context to free memory. Call when the context becomes overloaded.",
        parameters=_params(
            {"reason": {"type": "string", "description": "Reason for compaction"}}
        ),
    ),
    ToolDef(
        name="verify_implements",
        description="[Code] Verify that a code file implements requirements via @implements annotations. Returns passed, implemented count, and gaps.",
        parameters=_params(
            {
                "code_dir": {"type": "string", "description": "Path to code directory"},
                "file_path": {
                    "type": "string",
                    "description": "Path to the code file for analysis",
                },
            },
            ["code_dir", "file_path"],
        ),
    ),
    ToolDef(
        name="verify_traceability",
        description="[Code] Verify requirements traceability in code. Checks @implements coverage and reports gaps.",
        parameters=_params(
            {
                "code_dir": {"type": "string", "description": "Path to code directory"},
                "language": {
                    "type": "string",
                    "description": "Programming language (python, typescript)",
                },
            },
            ["code_dir"],
        ),
    ),
    ToolDef(
        name="annotate_code",
        description="[Code] Auto-annotate code with @implements decorators based on symbol names. dry_run=True shows changes without writing files.",
        parameters=_params(
            {
                "code_dir": {"type": "string", "description": "Path to code directory"},
                "dry_run": {
                    "type": "boolean",
                    "description": "If true, show what would be changed without modifying files",
                },
            },
            ["code_dir"],
        ),
    ),
    ToolDef(
        name="search_symbol",
        description="[Code] Find CLASS/FUNCTION/METHOD DEFINITIONS by name — the primary tool for symbol lookup. Uses AST parsers (not grep) for Python/TS/Go/Java/Rust. Returns structured data: symbol name, kind (class/function/method), file path, line number, decorators, and docstring. MUCH better than grep when you need to find where a class or function is DEFINED. Example: query='AgentWorker' finds 'class AgentWorker(Agent):' at persistent_agent.py:26.",
        parameters=_params(
            {
                "code_dir": {"type": "string", "description": "Path to code directory (defaults to project_path if omitted)"},
                "query": {"type": "string", "description": "Symbol name to search for (partial match)"},
            },
            ["query"],
        ),
    ),
]


# ======================================================================
# Handler registration
# ======================================================================


def _search_semantic_tool(
    code_dir: str, query: str, top_k: int, rebuild: bool
) -> dict:
    """Wrapper for search_semantic MCP tool."""
    from src.mcp.tools_semantic import search_semantic_tool

    return search_semantic_tool(
        project_path=code_dir or ".",
        query=query,
        top_k=top_k,
        rebuild=rebuild,
    )


def add_code_tools_handlers(
    handlers: dict[str, Callable],
    storage: StorageAdapter,
    methodology: Methodology,
    source_dir: str,
    spawner: Callable | None,
    agent_for_compact=None,
    srs_template_path: str = "srs_template.yaml",
) -> None:
    """Add code tool handlers to the handlers dict."""
    sd = source_dir or ""
    handlers.update(
        {
            "export_srs": lambda: export_srs_tool(storage, sd, srs_template_path),
            "compact_context": lambda reason="": compact_context_tool(
                agent_for_compact, reason
            ),
            "request_helper": lambda role, task: request_helper(spawner, role, task),
            "search_code": lambda code_dir="", pattern="": search_code_tool(
                code_dir or sd, pattern
            ),
            "get_file_tree": lambda code_dir="": get_file_tree_tool(code_dir or sd),
            "read_lints": lambda code_dir="", file_path=None: read_lints_tool(
                code_dir or sd, file_path
            ),
            "read_source_document": lambda filename=None: read_source_document(
                sd, filename
            ),
            "convert_source_file": lambda file_path: convert_source_file(file_path),
            "verify_implements": lambda code_dir="", file_path="": (
                verify_implements_tool(storage, code_dir or sd, file_path)
            ),
            "verify_traceability": lambda code_dir="", language="python": (
                verify_traceability_tool(storage, code_dir or sd, language)
            ),
            "annotate_code": lambda code_dir="", dry_run=True: annotate_code_tool(
                storage, code_dir or sd, dry_run
            ),
            "search_symbol": lambda code_dir="", query="": search_symbol_tool(
                code_dir or sd, query
            ),
            "search_semantic": lambda code_dir="", query="", top_k=10, rebuild=False: _search_semantic_tool(
                code_dir or sd, query, top_k, rebuild
            ),
        }
    )

"""Agent tools for working with code: read source/, search, verification."""

from pathlib import Path
from typing import Callable

from src.config.methodology import Methodology
from src.providers.base import ToolDef
from src.storage.adapter import StorageAdapter

# ======================================================================
# Tool functions
# ======================================================================


async def read_source(source_dir, filename: str | None = None) -> dict:
    """Read source documents from the source/ folder.
    Without arguments — file listing. With a filename — its contents."""
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
    path: "Path", lines: list, prefix: str = "", max_depth: int = 3, max_items: int = 200
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
            _walk(entry, lines, prefix + ("    " if is_last else "│   "), max_depth - 1, max_items)
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

    Args:
        code_dir: path to the code directory
        file_path: relative path to the file inside code_dir
    """
    from pathlib import Path

    from src.mcp.verifier import verify_implements

    full_path = Path(code_dir) / file_path
    report = verify_implements(storage, full_path)
    return {
        "passed": report.passed,
        "implemented": report.implemented,
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
) -> dict:
    """Check requirement coverage by code across the entire project.

    Returns coverage, list of implemented requirements and gaps.
    """
    from pathlib import Path

    from src.mcp.verifier import verify_traceability

    report = verify_traceability(storage, Path(code_dir), language)
    return {
        "passed": report.passed,
        "total_requirements": report.total_requirements,
        "implemented": report.implemented,
        "coverage": report.coverage,
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


# ======================================================================
# ToolDef entries
# ======================================================================


def _params(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


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
        name="read_source",
        description="[Export] Read source documents from source/ folder. No args — list files. With filename — file contents.",
        parameters=_params(
            {"filename": {"type": "string", "description": "Filename (optional)"}}
        ),
    ),
    ToolDef(
        name="search_code",
        description="[Code] Search project code (grep). Returns matching lines.",
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
]


# ======================================================================
# Handler registration
# ======================================================================


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
            "read_source": lambda filename=None: read_source(sd, filename),
            "verify_implements": lambda code_dir="", file_path="": (
                verify_implements_tool(storage, code_dir or sd, file_path)
            ),
            "verify_traceability": lambda code_dir="", language="python": (
                verify_traceability_tool(storage, code_dir or sd, language)
            ),
            "annotate_code": lambda code_dir="", dry_run=True: annotate_code_tool(
                storage, code_dir or sd, dry_run
            ),
        }
    )

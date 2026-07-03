"""Tree-sitter code chunker — extract functions, classes, methods from source files.

Language-agnostic: uses tree-sitter grammars for Python, TypeScript, Go, Java, Rust.
Each chunk = one symbol (function/class/method) with its source text and docstring.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class CodeChunk:
    """A single code symbol extracted for embedding."""

    rel_path: str       # relative to project root
    symbol: str         # function/class/method name
    kind: str           # "function", "class", "method"
    text: str           # source code (first 2000 chars)
    docstring: str      # extracted docstring or "" 
    line: int           # start line number


def chunk_project(project_path: Path) -> list[CodeChunk]:
    """Extract all code symbols from a project directory.

    Walks the project, parses each source file with tree-sitter,
    and returns a flat list of CodeChunk dataclasses.
    """
    chunks: list[CodeChunk] = []

    # Language → file extensions mapping
    lang_map = {
        "python": {".py"},
        "typescript": {".ts", ".tsx"},
        "javascript": {".js", ".jsx"},
        "go": {".go"},
        "java": {".java"},
        "rust": {".rs"},
    }

    for lang, extensions in sorted(lang_map.items()):
        for ext in extensions:
            for file_path in sorted(project_path.rglob(f"*{ext}")):
                if _should_skip(file_path, project_path):
                    continue
                try:
                    chunks.extend(_chunk_file(file_path, lang, project_path))
                except Exception:
                    pass  # skip unparsable files silently

    return chunks


def _chunk_file(
    file_path: Path, language: str, project_root: Path
) -> list[CodeChunk]:
    """Extract symbols from a single file."""
    from src.mcp.parsers.base_ts import (
        get_ts_parser,
        _node_type,
        _node_text,
        _node_children,
        _node_is_named,
        _root_node,
        _node_start_row,
        _node_type_to_kind,
    )

    parser = get_ts_parser(language)
    code_str = file_path.read_text(encoding="utf-8")
    code_bytes = code_str.encode("utf-8")

    try:
        tree = parser.parse(code_str)
    except Exception:
        return []

    root = _root_node(tree)
    chunks: list[CodeChunk] = []
    rel_path = str(file_path.relative_to(project_root))

    # Only extract these structural node types (not every named node)
    _STRUCTURAL = {
        "class_definition", "function_definition", "method_definition",
        "class_declaration", "function_declaration", "method_declaration",
        "function_item", "struct_item", "impl_item", "trait_item",
        "interface_declaration", "enum_declaration",
        "method", "function", "class",
    }

    def _walk(node) -> None:
        ntype = _node_type(node)

        if ntype in _STRUCTURAL:
            name = ""
            for child in _node_children(node):
                ctype = _node_type(child)
                if ctype in ("identifier", "name", "property_identifier"):
                    name = _node_text(child, code_bytes)
                    break

            if name:
                start_row = _node_start_row(node)
                text = _node_text(node, code_bytes)[:2000]
                docstring = _extract_docstring(node, code_bytes, language)
                kind = _node_type_to_kind(ntype)

                chunks.append(CodeChunk(
                    rel_path=rel_path,
                    symbol=name,
                    kind=kind,
                    text=text,
                    docstring=docstring,
                    line=start_row + 1,
                ))

        # Recurse
        for child in _node_children(node):
            if _node_is_named(child):
                _walk(child)

    _walk(root)
    return chunks


def _extract_docstring(node, code_bytes: bytes, language: str) -> str:
    """Extract docstring from a function/class node."""
    from src.mcp.parsers.base_ts import (
        _node_type,
        _node_text,
        _node_children,
        _node_is_named,
    )

    if language == "python":
        # Python: first expression_statement → string in body
        for child in _node_children(node):
            ntype = _node_type(child)
            if ntype == "block":
                for stmt in _node_children(child):
                    stype = _node_type(stmt)
                    if stype == "expression_statement":
                        for expr_child in _node_children(stmt):
                            etype = _node_type(expr_child)
                            if etype == "string":
                                text = _node_text(expr_child, code_bytes)
                                # Strip quotes
                                return text.strip('"\'').strip()[:500]
                    break  # only check first statement
            break

    # Generic: look for comment/string near the top of the body
    for child in _node_children(node):
        ntype = _node_type(child)
        if ntype in ("block", "body"):
            for stmt in _node_children(child):
                stype = _node_type(stmt)
                if stype in ("comment", "line_comment", "block_comment"):
                    text = _node_text(stmt, code_bytes)
                    # Strip comment markers
                    text = text.lstrip("/#* \t").strip()[:500]
                    return text
                if stype == "expression_statement":
                    for sc in _node_children(stmt):
                        if _node_type(sc) == "string":
                            text = _node_text(sc, code_bytes)
                            return text.strip('"\'').strip()[:500]
            break

    return ""


def _should_skip(file_path: Path, project_root: Path) -> bool:
    """Skip generated, vendored, and test fixture files."""
    parts = set(file_path.parts)
    skip_dirs = {
        "node_modules", ".git", "__pycache__", ".venv", "venv",
        "dist", "build", ".next", "out", "target", ".spec-editor",
        "test-results", "dry_run_output", ".vscode-test",
    }
    if parts & skip_dirs:
        return True
    name = file_path.name
    if any(name.endswith(s) for s in (".min.js", ".d.ts", ".generated.", ".test.")):
        return True
    return False

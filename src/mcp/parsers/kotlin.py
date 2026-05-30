"""Kotlin code parser: extraction of @implements annotations.

Uses tree-sitter-kotlin. Supports:
- @Implements("REQ-001") annotations on classes, functions, properties
- // @implements("REQ-001") in comments before symbols
"""

from pathlib import Path

from src.mcp.parsers.base_ts import make_ts_parser
from src.mcp.parsers.python import CodeAnnotation, CodeSymbol

# Tree-sitter Kotlin node types that represent code symbols
_KOTLIN_SYMBOL_TYPES: set[str] = {
    "class_declaration",
    "object_declaration",
    "interface_declaration",
    "function_declaration",
    "property_declaration",
}

parse_kotlin = make_ts_parser("kotlin", _KOTLIN_SYMBOL_TYPES)

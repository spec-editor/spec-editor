"""Java code parser: extraction of @implements annotations.

Uses tree-sitter-java. Supports:
- @Implements("REQ-001") annotations on classes, methods, constructors
- // @implements("REQ-001") in comments before symbols
"""

from pathlib import Path

from src.mcp.parsers.base_ts import make_ts_parser
from src.mcp.parsers.python import CodeAnnotation, CodeSymbol

# Tree-sitter Java node types that represent code symbols
_JAVA_SYMBOL_TYPES: set[str] = {
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
    "method_declaration",
    "constructor_declaration",
}

parse_java = make_ts_parser("java", _JAVA_SYMBOL_TYPES)

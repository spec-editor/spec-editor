"""Go code parser: extraction of @implements annotations.

Uses tree-sitter-go. Go has no decorators; @implements
is detected in comments preceding functions, methods, and type declarations.
"""

from pathlib import Path

from src.mcp.parsers.base_ts import make_ts_parser
from src.mcp.parsers.python import CodeAnnotation, CodeSymbol

# Tree-sitter Go node types that represent code symbols
_GO_SYMBOL_TYPES: set[str] = {
    "function_declaration",
    "method_declaration",
    "type_declaration",
}

parse_go = make_ts_parser("go", _GO_SYMBOL_TYPES)

"""Rust code parser: extraction of @implements annotations.

Uses tree-sitter-rust. Rust has no decorators; @implements
is detected in comments preceding functions, structs, enums, traits, and impl blocks.
"""

from pathlib import Path

from src.mcp.parsers.base_ts import make_ts_parser
from src.mcp.parsers.python import CodeAnnotation, CodeSymbol

# Tree-sitter Rust node types that represent code symbols
_RUST_SYMBOL_TYPES: set[str] = {
    "function_item",
    "struct_item",
    "enum_item",
    "trait_item",
    "impl_item",
}

parse_rust = make_ts_parser("rust", _RUST_SYMBOL_TYPES)

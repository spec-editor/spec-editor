from pathlib import Path
from src.mcp.parsers.base_ts import parse_with_ts
from src.mcp.parsers.python import CodeAnnotation, CodeSymbol

_SYMBOL_TYPES = {
    "function_item", "struct_item", "enum_item", "trait_item",
    "impl_item", "function_signature_item",
}

def parse_rust(file_path: Path) -> tuple[list[CodeAnnotation], list[CodeSymbol]]:
    return parse_with_ts(file_path, "rust", _SYMBOL_TYPES)

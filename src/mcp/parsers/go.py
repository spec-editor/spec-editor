from pathlib import Path
from src.mcp.parsers.base_ts import parse_with_ts
from src.mcp.parsers.python import CodeAnnotation, CodeSymbol

_SYMBOL_TYPES = {
    "function_declaration", "method_declaration", "type_declaration",
    "type_spec", "struct_type", "interface_type",
}

def parse_go(file_path: Path) -> tuple[list[CodeAnnotation], list[CodeSymbol]]:
    return parse_with_ts(file_path, "go", _SYMBOL_TYPES)

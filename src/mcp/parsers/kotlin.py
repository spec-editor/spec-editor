from pathlib import Path
from src.mcp.parsers.base_ts import parse_with_ts
from src.mcp.parsers.python import CodeAnnotation, CodeSymbol

_SYMBOL_TYPES = {
    "class_declaration", "object_declaration", "data_class_declaration",
    "function_declaration", "interface_declaration",
}

def parse_kotlin(file_path: Path) -> tuple[list[CodeAnnotation], list[CodeSymbol]]:
    return parse_with_ts(file_path, "kotlin", _SYMBOL_TYPES)

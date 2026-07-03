from pathlib import Path
from src.mcp.parsers.base_ts import parse_with_ts
from src.mcp.parsers.python import CodeAnnotation, CodeSymbol

_SYMBOL_TYPES = {
    "class_declaration", "interface_declaration", "enum_declaration",
    "method_declaration", "constructor_declaration",
}

def parse_java(file_path: Path) -> tuple[list[CodeAnnotation], list[CodeSymbol]]:
    return parse_with_ts(file_path, "java", _SYMBOL_TYPES)

"""TypeScript/JavaScript code parser: extraction of @implements annotations.

AST-based approach using tree-sitter and tree-sitter-languages.
Supports tree-sitter 0.23+ and 0.25+ (all attributes became methods in 0.25).
"""

import re
from pathlib import Path

import tree_sitter_language_pack as tsl

from src.mcp.parsers.python import CodeAnnotation, CodeSymbol

_IMPLEMENTS_RE = re.compile(r'@implements\("([^"]+)"\)', re.IGNORECASE)
_CLASS_DECL = "class_declaration"
_FUNC_DECL = "function_declaration"
_METHOD_DEF = "method_definition"
_VAR_DECL = "variable_declarator"
_ARROW_FUNC = "arrow_function"
_DECORATOR = "decorator"
_COMMENT = "comment"


def _call(val):
    """tree-sitter 0.25: all attributes are methods."""
    return val() if callable(val) else val


def _node_type(node) -> str:
    return _call(node.kind) if hasattr(node, "kind") else node.type


def _node_children(node):
    children = getattr(node, "children", None)
    if children is not None and not callable(children):
        yield from children
    else:
        for i in range(_call(node.child_count)):
            yield node.child(i)


def _node_start_row(node) -> int:
    sp = _call(getattr(node, "start_position", None) or getattr(node, "start_point", None))
    if sp is not None:
        return sp[0] if hasattr(sp, "__getitem__") else sp.row
    return 0


def _node_text(node, code_bytes: bytes) -> str:
    br = _call(node.byte_range)
    start, end = br if hasattr(br, "__getitem__") else (br.start, br.end)
    return code_bytes[start:end].decode("utf-8", errors="replace")


def _root_node(tree):
    rn = tree.root_node
    return rn() if callable(rn) else rn


def parse_typescript(file_path: Path) -> tuple[list[CodeAnnotation], list[CodeSymbol]]:
    if not file_path.exists():
        return [], []

    parser = _get_parser()
    code = file_path.read_text(encoding="utf-8")
    code_bytes = code.encode("utf-8")
    tree = parser.parse(code)
    root = _root_node(tree)

    symbols: list[CodeSymbol] = []
    comments: list[tuple[int, int, str]] = []

    _collect(root, code_bytes, symbols, comments)

    annotations: list[CodeAnnotation] = []
    for sym in symbols:
        sym_start_byte = _byte_offset(code_bytes, sym.line)
        req_ids: list[str] = list(getattr(sym, "_annotations", []))

        for c_start, c_end, c_text in comments:
            if c_end <= sym_start_byte:
                gap = code_bytes[c_end:sym_start_byte].decode("utf-8", errors="replace")
                if _is_whitespace_only(gap):
                    for rid in _extract_ids_from_text(c_text):
                        if rid not in req_ids:
                            req_ids.append(rid)

        annotations.extend(_make_annotations(req_ids, sym.name, sym.line, file_path))

    return annotations, symbols


def _collect(node, code_bytes: bytes, symbols, comments) -> None:
    nt = _node_type(node)

    if nt == _COMMENT:
        text = _node_text(node, code_bytes)
        comments.append((_call(node.start_byte), _call(node.end_byte), text))
        return

    if nt == _CLASS_DECL:
        sym = _extract_class(node, code_bytes)
        if sym is not None:
            symbols.append(sym)
    elif nt == _FUNC_DECL:
        sym = _extract_function(node, code_bytes)
        if sym is not None:
            symbols.append(sym)
    elif nt == _METHOD_DEF:
        sym = _extract_method(node, code_bytes)
        if sym is not None:
            symbols.append(sym)
    elif _is_arrow_declaration(node, code_bytes):
        sym = _extract_arrow(node, code_bytes)
        if sym is not None:
            symbols.append(sym)

    for child in _node_children(node):
        _collect(child, code_bytes, symbols, comments)


def _extract_class(node, code_bytes) -> CodeSymbol | None:
    name = _child_text(node, "type_identifier", code_bytes) or _child_text(node, "identifier", code_bytes)
    if not name:
        return None
    req_ids = _extract_decorator_ids(node, code_bytes)
    req_ids.extend(_prev_sibling_decorator_ids(node, code_bytes))
    sym = CodeSymbol(name=name, kind="class", line=_node_start_row(node) + 1)
    sym._annotations = req_ids  # type: ignore[attr-defined]
    return sym


def _extract_function(node, code_bytes) -> CodeSymbol | None:
    name = _child_text(node, "identifier", code_bytes)
    if not name:
        return None
    req_ids = _extract_decorator_ids(node, code_bytes)
    req_ids.extend(_prev_sibling_decorator_ids(node, code_bytes))
    sym = CodeSymbol(name=name, kind="function", line=_node_start_row(node) + 1)
    sym._annotations = req_ids
    return sym


def _extract_method(node, code_bytes) -> CodeSymbol | None:
    name = _child_text(node, "property_identifier", code_bytes)
    if not name:
        return None
    req_ids = _extract_decorator_ids(node, code_bytes)
    req_ids.extend(_prev_sibling_decorator_ids(node, code_bytes))
    sym = CodeSymbol(name=name, kind="method", line=_node_start_row(node) + 1)
    sym._annotations = req_ids
    return sym


def _extract_arrow(node, code_bytes) -> CodeSymbol | None:
    name = _child_text(node, "identifier", code_bytes)
    if not name:
        return None
    req_ids = _extract_decorator_ids(node, code_bytes)
    req_ids.extend(_prev_sibling_decorator_ids(node, code_bytes))
    sym = CodeSymbol(name=name, kind="function", line=_node_start_row(node) + 1)
    sym._annotations = req_ids
    return sym


def _is_arrow_declaration(node, code_bytes) -> bool:
    if _node_type(node) != _VAR_DECL:
        return False
    for child in _node_children(node):
        if _node_type(child) in (_ARROW_FUNC, "function"):
            return True
    return False


def _prev_sibling_decorator_ids(node, code_bytes) -> list[str]:
    parent = _call(getattr(node, "parent", lambda: None))
    if parent is None:
        return []
    ids = []
    for child in _node_children(parent):
        if _call(child.start_byte) >= _call(node.start_byte):
            break
        if _node_type(child) == _DECORATOR:
            ids.extend(_extract_ids_from_text(_node_text(child, code_bytes)))
    return ids


def _extract_decorator_ids(node, code_bytes) -> list[str]:
    ids = []
    for child in _node_children(node):
        if _node_type(child) == _DECORATOR:
            ids.extend(_extract_ids_from_text(_node_text(child, code_bytes)))
    return ids


def _extract_ids_from_text(text: str) -> list[str]:
    return [m.group(1) for m in _IMPLEMENTS_RE.finditer(text)]


def _child_text(node, child_type: str, code_bytes: bytes) -> str:
    for child in _node_children(node):
        if _node_type(child) == child_type:
            return _node_text(child, code_bytes)
    return ""


def _byte_offset(code: bytes, line_1based: int) -> int:
    pos = 0
    for _ in range(line_1based - 1):
        nl = code.find(b"\n", pos)
        if nl == -1:
            break
        pos = nl + 1
    return pos


def _is_whitespace_only(text: str) -> bool:
    return not text or text.isspace()


def _make_annotations(req_ids, symbol_name, line_no, file_path):
    return [
        CodeAnnotation(req_id=rid, symbol=symbol_name, line=line_no, file_path=str(file_path))
        for rid in req_ids
    ]


def _get_parser():
    """Create a fresh parser. The grammar is cached by tree_sitter_language_pack.
    
    A new Parser is returned on every call so that the parser is created
    and destroyed in the same thread — safe for use with asyncio.to_thread().
    """
    return tsl.get_parser("typescript")

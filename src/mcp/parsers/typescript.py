"""TypeScript/JavaScript code parser: extraction of @implements annotations.

AST-based approach using tree-sitter and tree-sitter-languages.
Supports:
- classes, functions, methods, arrow functions (const/let/var)
- @Decorator("...") decorators (experimental TS syntax)
- Comments: // @implements("REQ-001") or /* @implements("REQ-001") */
  on the line before declaration or as JSDoc @implements
"""

import re
from pathlib import Path

import tree_sitter_languages as tsl

from src.mcp.parsers.python import CodeAnnotation, CodeSymbol

# ---------------------------------------------------------------------------
# Regex for extracting req_id from text
# ---------------------------------------------------------------------------

_IMPLEMENTS_RE = re.compile(r'@implements\("([^"]+)"\)', re.IGNORECASE)

# tree-sitter node type names
_CLASS_DECL = "class_declaration"
_FUNC_DECL = "function_declaration"
_METHOD_DEF = "method_definition"
_VAR_DECL = "variable_declarator"
_ARROW_FUNC = "arrow_function"
_DECORATOR = "decorator"
_COMMENT = "comment"


def parse_typescript(file_path: Path) -> tuple[list[CodeAnnotation], list[CodeSymbol]]:
    """Extract @implements annotations and symbols from a TS/JS file (AST)."""
    if not file_path.exists():
        return [], []

    parser = _get_parser()
    code_bytes = file_path.read_bytes()
    tree = parser.parse(code_bytes)

    symbols: list[CodeSymbol] = []
    comments: list[tuple[int, int, str]] = []  # (start_byte, end_byte, text)

    _collect(tree.root_node, file_path, symbols, comments)

    # Match comments to symbols by position:
    # a comment relates to a symbol if it ends
    # immediately before the symbol start (within 2 lines)
    annotations: list[CodeAnnotation] = []
    for sym in symbols:
        sym_start_byte = _byte_offset(code_bytes, sym.line)
        # Decorators first (already bound to the symbol during collection)
        if hasattr(sym, "_annotations"):
            req_ids = list(sym._annotations)  # type: ignore[attr-defined]
        else:
            req_ids = []

        # Find comment that ends right before the symbol
        for c_start, c_end, c_text in comments:
            # Comment before symbol: ends before the symbol start
            if c_end <= sym_start_byte:
                # Check that only whitespace/newlines are between them
                gap = code_bytes[c_end:sym_start_byte].decode("utf-8", errors="replace")
                if _is_whitespace_only(gap):
                    ids = _extract_ids_from_text(c_text)
                    for rid in ids:
                        if rid not in req_ids:
                            req_ids.append(rid)

        annotations.extend(_make_annotations(req_ids, sym.name, sym.line, file_path))

    return annotations, symbols


def _collect(
    node,
    file_path: Path,
    symbols: list[CodeSymbol],
    comments: list[tuple[int, int, str]],
) -> None:
    """Recursively collect symbols and comments from the AST."""
    if node.type == _COMMENT:
        text = str(node.text, "utf-8")
        comments.append((node.start_byte, node.end_byte, text))
        return

    if node.type == _CLASS_DECL:
        sym = _extract_class(node)
        if sym is not None:
            symbols.append(sym)
    elif node.type == _FUNC_DECL:
        sym = _extract_function(node)
        if sym is not None:
            symbols.append(sym)
    elif node.type == _METHOD_DEF:
        sym = _extract_method(node)
        if sym is not None:
            symbols.append(sym)
    elif _is_arrow_declaration(node):
        sym = _extract_arrow(node)
        if sym is not None:
            symbols.append(sym)

    for child in node.children:
        _collect(child, file_path, symbols, comments)


# ---------------------------------------------------------------------------
# Extraction per node type
# ---------------------------------------------------------------------------


def _extract_class(node) -> CodeSymbol | None:
    """Extract class name and req_id from class_declaration."""
    name = _child_text(node, "type_identifier") or _child_text(node, "identifier")
    if not name:
        return None
    req_ids: list[str] = _extract_decorator_ids(node)
    req_ids.extend(_prev_sibling_decorator_ids(node))
    sym = CodeSymbol(name=name, kind="class", line=node.start_point[0] + 1)
    sym._annotations = req_ids  # type: ignore[attr-defined]
    return sym


def _extract_function(node) -> CodeSymbol | None:
    """Extract function name and req_id from function_declaration."""
    name = _child_text(node, "identifier")
    if not name:
        return None
    req_ids: list[str] = _extract_decorator_ids(node)
    req_ids.extend(_prev_sibling_decorator_ids(node))
    sym = CodeSymbol(name=name, kind="function", line=node.start_point[0] + 1)
    sym._annotations = req_ids  # type: ignore[attr-defined]
    return sym


def _extract_method(node) -> CodeSymbol | None:
    """Extract method name and req_id from method_definition.

    Method decorators can be children of method_definition
    or previous siblings in class_body (tree-sitter
    places them as neighbors rather than children).
    """
    name = _child_text(node, "property_identifier")
    if not name:
        return None
    req_ids: list[str] = _extract_decorator_ids(node)
    # Also check previous sibling decorators in parent
    req_ids.extend(_prev_sibling_decorator_ids(node))
    sym = CodeSymbol(name=name, kind="method", line=node.start_point[0] + 1)
    sym._annotations = req_ids  # type: ignore[attr-defined]
    return sym


def _extract_arrow(node) -> CodeSymbol | None:
    """Extract arrow function name from variable_declarator."""
    name = _child_text(node, "identifier")
    if not name:
        return None
    req_ids: list[str] = _extract_decorator_ids(node)
    req_ids.extend(_prev_sibling_decorator_ids(node))
    sym = CodeSymbol(name=name, kind="function", line=node.start_point[0] + 1)
    sym._annotations = req_ids  # type: ignore[attr-defined]
    return sym


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_arrow_declaration(node) -> bool:
    """variable_declarator containing arrow_function or function (anonymous)."""
    if node.type != _VAR_DECL:
        return False
    for child in node.children:
        if child.type in (_ARROW_FUNC, "function"):
            return True
    return False


def _prev_sibling_decorator_ids(node) -> list[str]:
    """Extract req_id from sibling decorators before the node in the parent.

    Needed for methods where tree-sitter places decorators
    as siblings inside class_body, not as children of method_definition.
    """
    parent = node.parent
    if parent is None:
        return []

    ids: list[str] = []
    for child in parent.children:
        if child.start_byte >= node.start_byte:
            break
        if child.type == _DECORATOR:
            ids.extend(_extract_ids_from_text(str(child.text, "utf-8")))
    return ids


def _extract_decorator_ids(node) -> list[str]:
    """Extract req_id from node decorators (e.g., @Implements('REQ-001'))."""
    ids: list[str] = []
    for child in node.children:
        if child.type == _DECORATOR:
            ids.extend(_extract_ids_from_text(str(child.text, "utf-8")))
    return ids


def _extract_ids_from_text(text: str) -> list[str]:
    """Extract all req_id from text (decorator, comment, JSDoc)."""
    return [m.group(1) for m in _IMPLEMENTS_RE.finditer(text)]


def _child_text(node, child_type: str) -> str:
    """Get text of the first child node of the specified type."""
    for child in node.children:
        if child.type == child_type:
            return str(child.text, "utf-8")
    return ""


def _byte_offset(code: bytes, line_1based: int) -> int:
    """Find byte offset of the start of a line (1-based)."""
    pos = 0
    for _ in range(line_1based - 1):
        nl = code.find(b"\n", pos)
        if nl == -1:
            break
        pos = nl + 1
    return pos


def _is_whitespace_only(text: str) -> bool:
    """Check that the string consists only of whitespace characters."""
    return not text or text.isspace()


def _make_annotations(
    req_ids: list[str],
    symbol_name: str,
    line_no: int,
    file_path: Path,
) -> list[CodeAnnotation]:
    return [
        CodeAnnotation(
            req_id=rid,
            symbol=symbol_name,
            line=line_no,
            file_path=str(file_path),
        )
        for rid in req_ids
    ]


# ---------------------------------------------------------------------------
# Cached parser
# ---------------------------------------------------------------------------

_ts_parser = None


def _get_parser():
    """Lazy initialization of tree-sitter parser for TypeScript."""
    global _ts_parser
    if _ts_parser is None:
        _ts_parser = tsl.get_parser("typescript")
    return _ts_parser

"""Shared helpers for tree-sitter based code parsers.

Used by language-specific parsers (Go, Java, Kotlin, Rust, etc.)
to extract @implements annotations and code symbols.

Supports tree-sitter 0.23+ and 0.25+ (all attributes are methods in 0.25).
"""

import re
from pathlib import Path

import tree_sitter_language_pack as tsl

from src.mcp.parsers.python import CodeAnnotation, CodeSymbol

_IMPLEMENTS_RE = re.compile(r'@implements\("([^"]+)"\)', re.IGNORECASE)

# tree-sitter 0.25 compatibility: all attributes are methods
_SKIP_CHILD_TYPES = frozenset(("{", "}", "(", ")", "[", "]", ",", ";", ".", ":", "::"))


def _call(val):
    """tree-sitter 0.25: all attributes are methods."""
    return val() if callable(val) else val


def _node_type(node) -> str:
    return _call(node.kind) if hasattr(node, "kind") else node.type


def _node_text(node, code_bytes: bytes) -> str:
    br = _call(node.byte_range)
    start, end = br if hasattr(br, "__getitem__") else (br.start, br.end)
    return code_bytes[start:end].decode("utf-8", errors="replace")


def _node_children(node):
    children = getattr(node, "children", None)
    if children is not None and not callable(children):
        yield from children
    else:
        for i in range(_call(node.child_count)):
            yield node.child(i)


def _node_start_row(node) -> int:
    sp = _call(
        getattr(node, "start_position", None) or getattr(node, "start_point", None)
    )
    if sp is not None:
        return sp[0] if hasattr(sp, "__getitem__") else sp.row
    return 0


def _node_is_named(node) -> bool:
    return _call(node.is_named)


def _node_parent(node):
    return _call(getattr(node, "parent", lambda: None))


def _root_node(tree):
    rn = tree.root_node
    return rn() if callable(rn) else rn


_parser_cache: dict[str, object] = {}


def get_ts_parser(language: str):
    if language not in _parser_cache:
        _parser_cache[language] = tsl.get_parser(language)
    return _parser_cache[language]


def extract_ids_from_text(text: str) -> list[str]:
    return [m.group(1) for m in _IMPLEMENTS_RE.finditer(text)]


def _node_type_to_kind(node_type: str) -> str:
    """Map tree-sitter node type to a CodeSymbol kind."""
    if any(k in node_type for k in ("class", "struct", "interface", "trait", "enum")):
        return "class"
    if any(k in node_type for k in ("function", "method", "constructor")):
        return "function"
    if "impl" in node_type:
        return "class"
    return "function"


# ── Main API ──


def parse_with_ts(
    file_path: Path,
    language: str,
    symbol_node_types: set[str],
) -> tuple[list[CodeAnnotation], list[CodeSymbol]]:
    if not file_path.exists():
        return [], []

    parser = get_ts_parser(language)
    code = file_path.read_text(encoding="utf-8")
    code_bytes = code.encode("utf-8")
    tree = parser.parse(code)
    root = _root_node(tree)

    symbols, comments = _walk_and_collect(root, code_bytes, symbol_node_types)

    annotations: list[CodeAnnotation] = []
    for sym in symbols:
        sym_start_byte = _byte_offset(code_bytes, sym.line)
        req_ids: list[str] = list(getattr(sym, "_annotations", []))

        for c_start, c_end, c_text in comments:
            if c_end <= sym_start_byte:
                gap = code_bytes[c_end:sym_start_byte].decode("utf-8", errors="replace")
                if _is_whitespace_only(gap):
                    for rid in extract_ids_from_text(c_text):
                        if rid not in req_ids:
                            req_ids.append(rid)

        annotations.extend(_make_annotations(req_ids, sym.name, sym.line, file_path))

    return annotations, symbols


def _walk_and_collect(root, code_bytes: bytes, symbol_node_types: set[str]):
    symbols: list[CodeSymbol] = []
    comments: list[tuple[int, int, str]] = []
    _walk(root, code_bytes, symbol_node_types, symbols, comments)
    return symbols, comments


def _walk(node, code_bytes: bytes, symbol_node_types, symbols, comments) -> None:
    nt = _node_type(node)

    if nt in ("comment", "line_comment", "block_comment"):
        text = _node_text(node, code_bytes)
        comments.append((_call(node.start_byte), _call(node.end_byte), text))
        return

    if nt in symbol_node_types:
        sym = _extract_symbol(node, code_bytes)
        if sym is not None:
            symbols.append(sym)

    for child in _node_children(node):
        _walk(child, code_bytes, symbol_node_types, symbols, comments)


def _extract_symbol(node, code_bytes: bytes) -> CodeSymbol | None:
    actual_node = node
    if _node_type(node) == "type_declaration":
        type_spec = _child_by_type(node, "type_spec", code_bytes)
        if type_spec is not None:
            actual_node = type_spec

    name = _find_name(actual_node, code_bytes)
    if name is None:
        return None

    sym = CodeSymbol(
        name=name,
        kind=_node_type_to_kind(_node_type(actual_node)),
        line=_node_start_row(node) + 1,
    )
    sym._annotations = _find_annotations_recursive(node, code_bytes)  # type: ignore[attr-defined]
    return sym


def _find_name(node, code_bytes: bytes) -> str | None:
    for child_type in (
        "name",
        "identifier",
        "field_identifier",
        "type_identifier",
        "simple_identifier",
    ):
        text = _child_text(node, child_type, code_bytes)
        if text:
            return text

    for child in _node_children(node):
        if _node_is_named(child) and _node_type(child) not in _SKIP_CHILD_TYPES:
            text = _node_text(child, code_bytes)
            if " " not in text and "\n" not in text:
                return text
    return None


def _find_annotations_recursive(node, code_bytes: bytes) -> list[str]:
    """Find annotations in DIRECT children and modifiers (not grandchildren).

    An annotation on a class should NOT propagate to its methods —
    each symbol owns only its direct annotations.
    """
    ids: list[str] = []
    for child in _node_children(node):
        if _node_type(child) in ("annotation", "attribute", "decorator"):
            ids.extend(extract_ids_from_text(_node_text(child, code_bytes)))
        if _node_type(child) == "modifiers":
            for gc in _node_children(child):
                if _node_type(gc) in ("annotation", "attribute", "decorator"):
                    ids.extend(extract_ids_from_text(_node_text(gc, code_bytes)))
    return ids


def _child_text(node, child_type: str, code_bytes: bytes) -> str:
    for child in _node_children(node):
        if _node_type(child) == child_type:
            return _node_text(child, code_bytes)
    return ""


def _child_by_type(node, child_type: str, code_bytes: bytes):
    for child in _node_children(node):
        if _node_type(child) == child_type:
            return child
    return None


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
        CodeAnnotation(
            req_id=rid, symbol=symbol_name, line=line_no, file_path=str(file_path)
        )
        for rid in req_ids
    ]

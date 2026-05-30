"""Shared helpers for tree-sitter based code parsers.

Used by language-specific parsers (Go, Java, Kotlin, Rust, C#, etc.)
to extract @implements annotations and code symbols.
"""

import re
from pathlib import Path

import tree_sitter_languages as tsl

from src.mcp.parsers.python import CodeAnnotation, CodeSymbol

# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------

_IMPLEMENTS_RE = re.compile(r'@implements\("([^"]+)"\)', re.IGNORECASE)


def extract_ids_from_text(text: str) -> list[str]:
    """Extract all req_id from text (decorator, comment, docstring)."""
    return [m.group(1) for m in _IMPLEMENTS_RE.finditer(text)]


# ---------------------------------------------------------------------------
# Tree-sitter parser cache
# ---------------------------------------------------------------------------

_parser_cache: dict[str, object] = {}


def get_ts_parser(language: str):
    """Lazy-init and cache a tree-sitter parser for the given language."""
    if language not in _parser_cache:
        _parser_cache[language] = tsl.get_parser(language)
    return _parser_cache[language]


# ---------------------------------------------------------------------------
# AST walking and symbol extraction
# ---------------------------------------------------------------------------


def collect_comments_and_symbols(
    root_node,
    symbol_node_types: set[str],
) -> tuple[list[CodeSymbol], list[tuple[int, int, str]]]:
    """Walk the AST and collect symbols and comments.

    Args:
        root_node: tree-sitter root node
        symbol_node_types: set of node type names that qualify as symbols

    Returns:
        (symbols, comments) where comments are (start_byte, end_byte, text)
    """
    symbols: list[CodeSymbol] = []
    comments: list[tuple[int, int, str]] = []

    _walk(root_node, symbol_node_types, symbols, comments)
    return symbols, comments


def _walk(
    node,
    symbol_node_types: set[str],
    symbols: list[CodeSymbol],
    comments: list[tuple[int, int, str]],
) -> None:
    """Recursively walk the AST."""
    if node.type in ("comment", "line_comment", "block_comment"):
        text = str(node.text, "utf-8")
        comments.append((node.start_byte, node.end_byte, text))
        return

    if node.type in symbol_node_types:
        sym = _extract_symbol_from_node(node)
        if sym is not None:
            symbols.append(sym)

    for child in node.children:
        _walk(child, symbol_node_types, symbols, comments)


def _extract_symbol_from_node(node) -> CodeSymbol | None:
    """Extract a CodeSymbol from a tree-sitter node.

    Handles language-specific name extraction:
    - Go: method_declaration uses field_identifier; type_declaration wraps type_spec
    - Java/Kotlin: identifier, type_identifier, simple_identifier
    - Rust: type_identifier for struct/enum/trait items
    - Annotations are found recursively in all descendants
    """
    # For Go type_declaration, look into type_spec for the actual name
    actual_node = node
    if node.type == "type_declaration":
        type_spec = _child_by_type(node, "type_spec")
        if type_spec is not None:
            actual_node = type_spec

    name = _find_name(actual_node)
    if name is None:
        return None

    sym = CodeSymbol(
        name=name,
        kind=_node_type_to_kind(actual_node.type),
        line=node.start_point[0] + 1,
        decorators=[],
        docstring="",
    )
    # Recursively find annotations in all descendants
    sym._annotations = _find_annotations_recursive(node)  # type: ignore[attr-defined]
    return sym


def _find_name(node) -> str | None:
    """Find the symbol name in a tree-sitter node.

    Tries common name-carrying child types across languages.
    """
    # Direct name-bearing children (tried in order)
    for child_type in (
        "name",
        "identifier",
        "field_identifier",  # Go method names
        "type_identifier",  # Java/Kotlin/Rust type names
        "simple_identifier",  # Kotlin function/property names
    ):
        text = _child_text(node, child_type)
        if text:
            return text

    # Fallback: first reasonable named child (single-word text only)
    for child in node.children:
        if child.is_named and child.type not in _SKIP_CHILD_TYPES:
            text = str(child.text, "utf-8")
            if " " not in text and "\n" not in text:
                return text

    return None


def _find_annotations_recursive(node) -> list[str]:
    """Search for annotation/decorator nodes in DIRECT children and modifiers.

    Does NOT recurse into grandchildren — an annotation on a method
    should not propagate to the enclosing class.
    Checks modifiers container (Java/Kotlin put annotations inside modifiers).
    """
    ids: list[str] = []
    for child in node.children:
        if child.type in ("annotation", "attribute", "decorator"):
            ids.extend(extract_ids_from_text(str(child.text, "utf-8")))
        # Also check modifiers container
        if child.type == "modifiers":
            for gc in child.children:
                if gc.type in ("annotation", "attribute", "decorator"):
                    ids.extend(extract_ids_from_text(str(gc.text, "utf-8")))
    return ids


# Child types to skip when looking for names in fallback
_SKIP_CHILD_TYPES = frozenset(
    {
        "comment",
        "line_comment",
        "block_comment",
        "annotation",
        "attribute",
        "decorator",
        "modifiers",
        "visibility_modifier",
        "parameters",
        "parameter",
        "formal_parameters",
        "type_parameters",
        "type_arguments",
        "block",
        "body",
        "class_body",
        "declaration_list",
        "return_type",
        "void_type",
        "parameter_list",
        "field_declaration_list",
        "enum_variant_list",
        "enum_variant",
    }
)


def _node_type_to_kind(node_type: str) -> str:
    """Map tree-sitter node type to a CodeSymbol kind."""
    if (
        "class" in node_type
        or "struct" in node_type
        or "interface" in node_type
        or "trait" in node_type
        or "enum" in node_type
    ):
        return "class"
    if "function" in node_type or "method" in node_type or "constructor" in node_type:
        return "function"
    if "impl" in node_type:
        return "class"
    return "function"


# ---------------------------------------------------------------------------
# Comment-to-symbol matching
# ---------------------------------------------------------------------------


def match_comments_to_symbols(
    symbols: list[CodeSymbol],
    comments: list[tuple[int, int, str]],
    code_bytes: bytes,
) -> list[CodeAnnotation]:
    """Match comments to the symbols they precede.

    A comment belongs to a symbol if:
    - It ends before the symbol starts
    - Only whitespace/newlines separate them
    """
    annotations: list[CodeAnnotation] = []

    for sym in symbols:
        sym_start_byte = _byte_offset(code_bytes, sym.line)
        req_ids = list(getattr(sym, "_annotations", []))

        for c_start, c_end, c_text in comments:
            if c_end <= sym_start_byte:
                gap = code_bytes[c_end:sym_start_byte].decode("utf-8", errors="replace")
                if _is_whitespace_only(gap):
                    ids = extract_ids_from_text(c_text)
                    for rid in ids:
                        if rid not in req_ids:
                            req_ids.append(rid)

        annotations.extend(
            CodeAnnotation(
                req_id=rid,
                symbol=sym.name,
                line=sym.line,
                file_path="",  # filled by caller
            )
            for rid in req_ids
        )

    return annotations


# ---------------------------------------------------------------------------
# Byte/string utilities
# ---------------------------------------------------------------------------


def _child_text(node, child_type: str) -> str | None:
    """Get text of the first child node of the specified type."""
    for child in node.children:
        if child.type == child_type:
            return str(child.text, "utf-8")
    return None


def _child_by_type(node, child_type: str):
    """Get the first child node of the specified type."""
    for child in node.children:
        if child.type == child_type:
            return child
    return None


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


# ---------------------------------------------------------------------------
# Public: generic tree-sitter parser factory
# ---------------------------------------------------------------------------


def make_ts_parser(
    language: str,
    symbol_node_types: set[str],
):
    """Create a parse function for a tree-sitter language.

    Args:
        language: tree-sitter language name (e.g. 'go', 'java', 'kotlin', 'rust')
        symbol_node_types: node types to treat as symbols

    Returns:
        A parse_<lang>(file_path: Path) -> (annotations, symbols) function
    """

    def parse_file(file_path: Path) -> tuple[list[CodeAnnotation], list[CodeSymbol]]:
        if not file_path.exists():
            return [], []

        parser = get_ts_parser(language)
        code_bytes = file_path.read_bytes()
        tree = parser.parse(code_bytes)

        symbols, comments = collect_comments_and_symbols(
            tree.root_node, symbol_node_types
        )

        annotations = match_comments_to_symbols(symbols, comments, code_bytes)

        # Fill in file_path
        for ann in annotations:
            ann.file_path = str(file_path)

        return annotations, symbols

    return parse_file

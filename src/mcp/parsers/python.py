"""Python code parser: extraction of @implements annotations."""

import ast
from pathlib import Path

from pydantic import BaseModel, Field


class CodeAnnotation(BaseModel):
    """@implements annotation in code."""

    req_id: str = ""
    symbol: str = ""  # function/class name
    line: int = 0
    file_path: str = ""


class CodeSymbol(BaseModel):
    """Code symbol: class, function, method."""

    name: str = ""
    kind: str = ""  # class, function, method, route
    line: int = 0
    decorators: list[str] = Field(default_factory=list)
    docstring: str = ""


def parse_python(file_path: Path) -> tuple[list[CodeAnnotation], list[CodeSymbol]]:
    """Extract @implements annotations and symbols from a Python file."""
    if not file_path.exists():
        return [], []

    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    annotations: list[CodeAnnotation] = []
    symbols: list[CodeSymbol] = []

    for node in ast.walk(tree):
        # Classes
        if isinstance(node, ast.ClassDef):
            sym = CodeSymbol(
                name=node.name,
                kind="class",
                line=node.lineno,
                decorators=_get_decorators(node),
            )
            symbols.append(sym)
            _extract_annotations(node, sym.name, file_path, annotations)

        # Functions
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sym = CodeSymbol(
                name=node.name,
                kind="function",
                line=node.lineno,
                decorators=_get_decorators(node),
            )
            symbols.append(sym)
            _extract_annotations(node, sym.name, file_path, annotations)

    return annotations, symbols


def _get_decorators(node: ast.AST) -> list[str]:
    """Get decorator names."""
    result = []
    if hasattr(node, "decorator_list"):
        for d in node.decorator_list:
            if isinstance(d, ast.Call):
                if isinstance(d.func, ast.Name):
                    result.append(d.func.id)
            elif isinstance(d, ast.Name):
                result.append(d.id)
    return result


def _extract_annotations(
    node, symbol_name: str, file_path: Path, result: list[CodeAnnotation]
) -> None:
    if hasattr(node, "decorator_list"):
        for d in node.decorator_list:
            if (
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Name)
                and d.func.id == "implements"
            ):
                for arg in d.args:
                    if isinstance(arg, ast.Constant):
                        result.append(
                            CodeAnnotation(
                                req_id=arg.value,
                                symbol=symbol_name,
                                line=node.lineno,
                                file_path=str(file_path),
                            )
                        )

    # Search for @implements in docstring
    doc = ast.get_docstring(node)
    if doc:
        import re

        for match in re.finditer(r'@implements\("([^"]+)"\)', doc):
            result.append(
                CodeAnnotation(
                    req_id=match.group(1),
                    symbol=symbol_name,
                    line=node.lineno,
                    file_path=str(file_path),
                )
            )

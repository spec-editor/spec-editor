"""Traceability decorator: links code symbols to specification elements.

Usage:
    from src.tracing import implements

    @implements("ent-category")
    class Category(Base):
        ...
"""

from typing import Callable, TypeVar

T = TypeVar("T", bound=type)


def implements(req_id: str) -> Callable[[T], T]:
    """Decorator linking a class/function to its specification requirement.

    The req_id is extracted by the AST parser (src/mcp/parsers/python.py)
    for bidirectional traceability verification.
    """

    def decorator(target: T) -> T:
        target.__implements__ = req_id
        return target

    return decorator

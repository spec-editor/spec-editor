"""Implementation Framework — structured code generation.

Three-layer framework for generating architecture-compliant code
from specification elements.

Layers:
    1. Architectural Pattern   — ``patterns/``   (hexagonal, clean, ddd, mvc, none)
    2. Coding Templates        — ``templates/``  (copier, jinja2, none)
    3. Architecture Enforcement — ``enforcement/`` (pytest, pytest_arch, import_linter, none)

Usage::

    from src.implementation import create_implementation_engine

    engine = create_implementation_engine(project_path)

    # Initialize new project
    engine.initialize_project()

    # Generate code for an element
    files = engine.generate(element)

    # Verify architecture compliance
    violations = engine.verify()
"""

from src.implementation.engine import ImplementationEngine
from src.implementation.patterns import (
    ArchPattern,
    CleanArchitecturePattern,
    DDDPattern,
    HexagonalPattern,
    MVCPattern,
    NoPattern,
    get_pattern,
    list_patterns,
)
from src.implementation.templates import (
    TemplateEngine,
    CopierTemplateEngine,
    Jinja2TemplateEngine,
    NoopTemplateEngine,
    create_template_engine,
)
from src.implementation.enforcement import (
    ArchEnforcer,
    ArchViolation,
    PytestArchEnforcer,
    PytestPlainEnforcer,
    ImportLinterEnforcer,
    NoopEnforcer,
    create_arch_enforcer,
)


def create_implementation_engine(project_path: str) -> ImplementationEngine:
    """Create an ImplementationEngine for a project (convenience function).

    Equivalent to ``ImplementationEngine(project_path)``.
    """
    return ImplementationEngine(project_path)


__all__ = [
    # Engine
    "ImplementationEngine",
    "create_implementation_engine",
    # Patterns
    "ArchPattern",
    "HexagonalPattern",
    "CleanArchitecturePattern",
    "DDDPattern",
    "MVCPattern",
    "NoPattern",
    "get_pattern",
    "list_patterns",
    # Templates
    "TemplateEngine",
    "CopierTemplateEngine",
    "Jinja2TemplateEngine",
    "NoopTemplateEngine",
    "create_template_engine",
    # Enforcement
    "ArchEnforcer",
    "ArchViolation",
    "PytestArchEnforcer",
    "PytestPlainEnforcer",
    "ImportLinterEnforcer",
    "NoopEnforcer",
    "create_arch_enforcer",
]

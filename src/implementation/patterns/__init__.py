"""Architectural Pattern — Layer 1 of the Implementation Framework.

Defines the structural rules, directory layout, and dependency constraints
for generated code. Patterns are declared in ``methodology.yaml`` →
``implementation:`` section.

Each pattern provides:
    - Directory structure (folders to create)
    - Layer rules (which layer can depend on which)
    - Naming conventions (how to name files per element type)
    - Template context (variables passed to code generation templates)

Patterns:
    - ``hexagonal`` — Ports & Adapters (Hexagonal Architecture)
    - ``clean`` — Clean Architecture (Entities → Use Cases → Adapters)
    - ``ddd`` — Domain-Driven Design tactical patterns
    - ``mvc`` — Model-View-Controller (classic web apps)
    - ``none`` — No pattern (flat structure, no rules)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class ArchPattern(ABC):
    """Abstract architectural pattern definition."""

    # Human-readable name
    name: str = ""

    @abstractmethod
    def get_layers(self) -> list[dict[str, Any]]:
        """Return layer definitions.

        Each layer dict:
            - name: str — layer name (e.g. "domain")
            - path: str — relative directory path
            - allowed_deps: list[str] — layers this layer may import from
            - description: str — what goes in this layer
        """
        ...

    @abstractmethod
    def get_naming_conventions(self) -> dict[str, str]:
        """Return file naming patterns per element type.

        Keys are element type prefixes (MOD, ENT, NFR, SCN, SEC...),
        values are file naming templates (e.g. ``{id_lower}_service.py``).
        """
        ...

    def get_directory_structure(self, project_path: Path) -> list[Path]:
        """Return list of directories to create for this pattern."""
        dirs: list[Path] = []
        for layer in self.get_layers():
            dirs.append(project_path / layer["path"])
        return dirs

    def get_dependency_rules(self) -> list[dict[str, Any]]:
        """Return import dependency rules for architecture enforcement.

        Each rule dict:
            - from_layer: str — source layer
            - from_path: str — glob pattern for source files
            - should_not_import: list[str] — forbidden import patterns
            - description: str — human-readable explanation
        """
        rules: list[dict[str, Any]] = []
        layers: dict[str, dict[str, Any]] = {l["name"]: l for l in self.get_layers()}
        for layer_name, layer in layers.items():
            allowed: list[str] = layer.get("allowed_deps", [])
            forbidden_paths: list[str] = []
            for other_name, other_layer in layers.items():
                if other_name == layer_name:
                    continue  # self-reference is always allowed
                if other_name not in allowed:
                    forbidden_paths.append(other_layer["path"].rstrip("/"))
            if forbidden_paths:
                rules.append({
                    "from_path": f'{layer["path"]}**',
                    "from_layer": layer_name,
                    "should_not_import": forbidden_paths,
                    "description": (
                        f'{layer_name} must not depend on: '
                        f'{", ".join(forbidden_paths)}'
                    ),
                })
        return rules

    def get_template_context(self, element: Any) -> dict[str, Any]:
        """Return extra template variables for code generation.

        Override in subclasses to provide pattern-specific context.
        """
        return {}


# ── Pattern implementations ────────────────────────────────────────


class HexagonalPattern(ArchPattern):
    """Hexagonal Architecture (Ports & Adapters).

    Three layers:
        - domain/      — business logic, entities, value objects
        - ports/       — interfaces (repositories, services)
        - adapters/    — implementations (DB, HTTP, CLI, tests)
    """

    name = "hexagonal"

    def get_layers(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "domain",
                "path": "src/domain/",
                "allowed_deps": [],  # domain depends on nothing
                "description": "Business entities, value objects, domain services, domain events",
            },
            {
                "name": "ports",
                "path": "src/ports/",
                "allowed_deps": ["domain"],
                "description": "Interfaces/abstract base classes: repositories, services, event buses",
            },
            {
                "name": "adapters",
                "path": "src/adapters/",
                "allowed_deps": ["domain", "ports"],
                "description": "Concrete implementations: database, HTTP API, CLI, message queue",
            },
            {
                "name": "tests",
                "path": "tests/",
                "allowed_deps": ["domain", "ports", "adapters"],
                "description": "All tests (unit, integration, architecture)",
            },
        ]

    def get_naming_conventions(self) -> dict[str, str]:
        return {
            "MOD": "{id_lower}_service.py",
            "ENT": "{id_lower}_entity.py",
            "NFR": "{id_lower}_middleware.py",
            "SCN": "test_{id_lower}_scenario.py",
            "SEC": "{id_lower}_component.pyx",
        }

    def get_template_context(self, element: Any) -> dict[str, Any]:
        element_type = getattr(element, "element_type", "")
        ctx: dict[str, Any] = {
            "layers": {
                "domain": "src/domain/",
                "ports": "src/ports/",
                "adapters": "src/adapters/",
            }
        }
        if element_type == "module":
            ctx.update({
                "needs_port_interface": True,
                "needs_adapter_impl": True,
            })
        elif element_type == "entity":
            ctx.update({
                "base_class": "DomainEntity",
                "needs_value_objects": True,
            })
        return ctx


class CleanArchitecturePattern(ArchPattern):
    """Clean Architecture (Robert C. Martin).

    Four concentric layers:
        - entities/    — enterprise business rules
        - use_cases/   — application business rules
        - interfaces/  — controllers, gateways, presenters
        - frameworks/  — DB, web, devices, external
    """

    name = "clean"

    def get_layers(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "entities",
                "path": "src/entities/",
                "allowed_deps": [],
                "description": "Enterprise-wide business rules and data structures",
            },
            {
                "name": "use_cases",
                "path": "src/use_cases/",
                "allowed_deps": ["entities"],
                "description": "Application-specific business rules (interactors)",
            },
            {
                "name": "interfaces",
                "path": "src/interfaces/",
                "allowed_deps": ["entities", "use_cases"],
                "description": "Controllers, gateways, presenters (adapts outer → inner)",
            },
            {
                "name": "frameworks",
                "path": "src/frameworks/",
                "allowed_deps": ["entities", "use_cases", "interfaces"],
                "description": "Database drivers, web frameworks, external services",
            },
        ]

    def get_naming_conventions(self) -> dict[str, str]:
        return {
            "MOD": "{id_lower}_interactor.py",
            "ENT": "{id_lower}_entity.py",
            "NFR": "{id_lower}_gateway.py",
            "SCN": "test_{id_lower}_e2e.py",
        }


class DDDPattern(ArchPattern):
    """Domain-Driven Design tactical patterns.

    Structured around Aggregates, Entities, Value Objects,
    Repositories, Domain Events, and Domain Services.
    """

    name = "ddd"

    def get_layers(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "domain",
                "path": "src/domain/",
                "allowed_deps": [],
                "description": (
                    "Aggregates, Entities, Value Objects, Domain Events, "
                    "Repository interfaces, Domain Services"
                ),
            },
            {
                "name": "application",
                "path": "src/application/",
                "allowed_deps": ["domain"],
                "description": "Application services, commands, queries, DTOs",
            },
            {
                "name": "infrastructure",
                "path": "src/infrastructure/",
                "allowed_deps": ["domain", "application"],
                "description": "Repository implementations, ORM, external APIs, messaging",
            },
            {
                "name": "presentation",
                "path": "src/presentation/",
                "allowed_deps": ["application"],
                "description": "REST controllers, GraphQL resolvers, CLI commands",
            },
        ]

    def get_naming_conventions(self) -> dict[str, str]:
        return {
            "MOD": "{id_lower}_aggregate.py",
            "ENT": "{id_lower}_entity.py",
            "NFR": "{id_lower}_domain_service.py",
            "SCN": "test_{id_lower}_integration.py",
        }

    def get_template_context(self, element: Any) -> dict[str, Any]:
        element_type = getattr(element, "element_type", "")
        ctx: dict[str, Any] = {
            "ddd_patterns": {
                "aggregate_root": True,
                "value_objects": True,
                "domain_events": True,
                "repository_pattern": True,
                "ubiquitous_language": True,
            }
        }
        if element_type == "entity":
            ctx["ddd_type"] = "entity"
            ctx["needs_repository"] = True
        elif element_type == "module":
            ctx["ddd_type"] = "aggregate_root"
            ctx["needs_factory"] = True
        return ctx


class MVCPattern(ArchPattern):
    """Model-View-Controller (classic web application pattern)."""

    name = "mvc"

    def get_layers(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "models",
                "path": "src/models/",
                "allowed_deps": [],
                "description": "Data models, ORM classes, business logic",
            },
            {
                "name": "views",
                "path": "src/views/",
                "allowed_deps": ["models"],
                "description": "Templates, serializers, presenters",
            },
            {
                "name": "controllers",
                "path": "src/controllers/",
                "allowed_deps": ["models", "views"],
                "description": "Route handlers, request processing, business orchestration",
            },
        ]

    def get_naming_conventions(self) -> dict[str, str]:
        return {
            "MOD": "{id_lower}_controller.py",
            "ENT": "{id_lower}_model.py",
            "NFR": "{id_lower}_middleware.py",
            "SCN": "test_{id_lower}_acceptance.py",
        }


class NoPattern(ArchPattern):
    """No architectural pattern — flat structure, minimal constraints."""

    name = "none"

    def get_layers(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "src",
                "path": "src/",
                "allowed_deps": ["src/", "tests/", "lib/", "vendor/"],
                "description": "All source code",
            },
        ]

    def get_naming_conventions(self) -> dict[str, str]:
        return {
            "MOD": "{id_lower}.py",
            "ENT": "{id_lower}.py",
            "NFR": "{id_lower}.py",
        }


# ── Registry ────────────────────────────────────────────────────────

_PATTERN_REGISTRY: dict[str, type[ArchPattern]] = {
    "hexagonal": HexagonalPattern,
    "clean": CleanArchitecturePattern,
    "ddd": DDDPattern,
    "mvc": MVCPattern,
    "none": NoPattern,
}


def get_pattern(name: str) -> ArchPattern:
    """Get an architectural pattern by name.

    Args:
        name: Pattern name (``hexagonal``, ``clean``, ``ddd``, ``mvc``, ``none``)

    Returns:
        Instantiated pattern.

    Raises:
        ValueError if pattern not found.
    """
    cls = _PATTERN_REGISTRY.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown architectural pattern: '{name}'. "
            f"Available: {', '.join(_PATTERN_REGISTRY)}"
        )
    return cls()


def list_patterns() -> list[str]:
    """List all available pattern names."""
    return list(_PATTERN_REGISTRY)

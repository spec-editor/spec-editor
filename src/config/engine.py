"""MethodologyEngine — central entry point for methodology operations.

CA-005: Provides methodology loading, validation of specification elements
against methodology rules, and connectivity metrics computation.
"""

from pathlib import Path

from src.config.methodology import (
    AspectDef,
    Methodology,
    get_aspect,
    get_element_type,
    get_hierarchy,
    get_relationship_type,
    load_methodology,
)
from src.mcp.metrics import MetricsReport, compute_metrics
from src.mcp.validator import ValidationReport, validate as run_validate
from src.storage.adapter import StorageAdapter


class MethodologyEngine:
    """Central entry point for methodology operations.

    Wraps a Methodology instance and provides:
    - Validation of specification elements against methodology rules
    - Connectivity metrics computation
    - Aspect and element type queries
    - Methodology introspection
    """

    def __init__(self, methodology: Methodology) -> None:
        self._methodology = methodology

    @classmethod
    def from_path(cls, path: Path) -> "MethodologyEngine":
        """Load methodology from a YAML file path and wrap it."""
        methodology = load_methodology(path)
        return cls(methodology)

    @classmethod
    def from_project(cls, project_path: Path) -> "MethodologyEngine":
        """Load methodology from a project directory (methodology.yaml)."""
        method_path = project_path / "methodology.yaml"
        return cls.from_path(method_path)

    # ── Methodology introspection ──

    @property
    def methodology(self) -> Methodology:
        return self._methodology

    @property
    def name(self) -> str:
        return self._methodology.name

    @property
    def version(self) -> str:
        return self._methodology.version

    @property
    def description(self) -> str:
        return self._methodology.description

    @property
    def aspects(self) -> list[AspectDef]:
        return list(self._methodology.aspects)

    @property
    def skills(self) -> list[str]:
        return list(self._methodology.skills)

    def get_aspect(self, name: str) -> AspectDef | None:
        """Find an aspect by name."""
        return get_aspect(self._methodology, name)

    def list_aspect_names(self) -> list[str]:
        """Return names of all defined aspects."""
        return [a.name for a in self._methodology.aspects]

    def get_element_type(self, aspect_name: str, type_name: str):
        """Find an element type in the specified aspect."""
        return get_element_type(self._methodology, aspect_name, type_name)

    def get_relationship_type(self, name: str):
        """Find a relationship type by name (search across all aspects)."""
        return get_relationship_type(self._methodology, name)

    def get_hierarchy(self, aspect_name: str) -> dict[str, str | None]:
        """Return parent-child hierarchy for an aspect."""
        return get_hierarchy(self._methodology, aspect_name)

    def format(self) -> str:
        """Return formatted methodology text for agent prompts."""
        from src.config.methodology import format_methodology

        return format_methodology(self._methodology)

    # ── Validation ──

    def validate(
        self, storage: StorageAdapter, fix: bool = True
    ) -> ValidationReport:
        """Validate specification elements against methodology rules.

        Args:
            storage: Storage to read elements from.
            fix: If True, auto-fix broken references.

        Returns:
            ValidationReport with errors, warnings, and fix count.
        """
        return run_validate(storage, self._methodology, fix=fix)

    # ── Metrics ──

    def compute_metrics(self, storage: StorageAdapter) -> MetricsReport:
        """Compute connectivity metrics for the specification.

        Args:
            storage: Storage to read elements from.

        Returns:
            MetricsReport with counts, connectivity, orphans, coverage.
        """
        return compute_metrics(storage)

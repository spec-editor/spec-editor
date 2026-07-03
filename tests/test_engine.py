"""Tests for MethodologyEngine (CA-005 / MOD-005)."""

from pathlib import Path

import pytest
import yaml

from src.config.engine import MethodologyEngine
from src.config.methodology import Methodology, load_methodology
from src.storage.models import Element, ElementStatus


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


MINIMAL_METHODOLOGY = {
    "name": "minimal",
    "version": "1.0",
    "description": "Minimal methodology for testing",
    "aspects": [
        {
            "name": "features",
            "title": "Features",
            "description": "Feature list",
            "element_types": [
                {
                    "name": "feature",
                    "title": "Feature",
                    "attributes": [
                        {"name": "priority", "type": "string", "title": "Priority"}
                    ],
                }
            ],
            "relationship_types": [
                {
                    "name": "depends_on",
                    "title": "Depends on",
                    "source_aspects": ["features"],
                    "target_aspects": ["features"],
                    "cardinality": "many-to-many",
                }
            ],
        }
    ],
    "skills": ["analyst"],
}

WATERFALL_SNIPPET = {
    "name": "waterfall",
    "version": "1.0",
    "description": "Waterfall methodology",
    "aspects": [
        {
            "name": "modules",
            "title": "Modules",
            "description": "Modules",
            "element_types": [
                {"name": "module", "title": "Module", "attributes": []},
                {"name": "component", "title": "Component", "attributes": []},
            ],
            "relationship_types": [
                {
                    "name": "consists_of",
                    "title": "Consists of",
                    "source_aspects": ["modules"],
                    "target_aspects": ["modules"],
                    "cardinality": "1-to-many",
                }
            ],
        },
        {
            "name": "data_entities",
            "title": "Data Entities",
            "description": "Data model",
            "element_types": [
                {"name": "entity", "title": "Entity", "attributes": []},
                {"name": "field", "title": "Field", "attributes": []},
            ],
            "relationship_types": [
                {
                    "name": "consists_of",
                    "title": "Consists of",
                    "source_aspects": ["data_entities"],
                    "target_aspects": ["data_entities"],
                    "cardinality": "1-to-many",
                },
                {
                    "name": "references",
                    "title": "References",
                    "source_aspects": ["data_entities"],
                    "target_aspects": ["data_entities"],
                    "cardinality": "many-to-many",
                },
            ],
        },
    ],
    "skills": ["system_analyst"],
}


# ======================================================================
# Test fixtures
# ======================================================================


@pytest.fixture
def minimal_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "minimal.yaml"
    _write_yaml(path, MINIMAL_METHODOLOGY)
    return path


@pytest.fixture
def waterfall_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "waterfall.yaml"
    _write_yaml(path, WATERFALL_SNIPPET)
    return path


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Create a fake project directory with methodology.yaml."""
    _write_yaml(tmp_path / "methodology.yaml", MINIMAL_METHODOLOGY)
    return tmp_path


# ======================================================================
# Construction
# ======================================================================


class TestMethodologyEngineConstruction:
    def test_from_path(self, minimal_yaml: Path):
        engine = MethodologyEngine.from_path(minimal_yaml)
        assert engine.name == "minimal"
        assert engine.version == "1.0"

    def test_from_project(self, project_dir: Path):
        engine = MethodologyEngine.from_project(project_dir)
        assert engine.name == "minimal"
        assert engine.version == "1.0"

    def test_from_path_missing_raises(self):
        with pytest.raises(FileNotFoundError):
            MethodologyEngine.from_path(Path("/nonexistent/methodology.yaml"))

    def test_direct_construction(self, minimal_yaml: Path):
        methodology = load_methodology(minimal_yaml)
        engine = MethodologyEngine(methodology)
        assert engine.name == "minimal"

    def test_from_project_no_methodology_yaml(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            MethodologyEngine.from_project(tmp_path / "empty_dir")


# ======================================================================
# Introspection properties
# ======================================================================


class TestMethodologyEngineIntrospection:
    def test_name(self, minimal_yaml: Path):
        engine = MethodologyEngine.from_path(minimal_yaml)
        assert engine.name == "minimal"

    def test_version(self, minimal_yaml: Path):
        engine = MethodologyEngine.from_path(minimal_yaml)
        assert engine.version == "1.0"

    def test_description(self, minimal_yaml: Path):
        engine = MethodologyEngine.from_path(minimal_yaml)
        assert engine.description == "Minimal methodology for testing"

    def test_aspects(self, minimal_yaml: Path):
        engine = MethodologyEngine.from_path(minimal_yaml)
        aspects = engine.aspects
        assert len(aspects) == 1
        assert aspects[0].name == "features"

    def test_skills(self, minimal_yaml: Path):
        engine = MethodologyEngine.from_path(minimal_yaml)
        assert engine.skills == ["analyst"]

    def test_methodology_property(self, minimal_yaml: Path):
        engine = MethodologyEngine.from_path(minimal_yaml)
        assert isinstance(engine.methodology, Methodology)
        assert engine.methodology.name == "minimal"


# ======================================================================
# Aspect / element type / relationship queries
# ======================================================================


class TestMethodologyEngineQueries:
    def test_get_aspect_found(self, waterfall_yaml: Path):
        engine = MethodologyEngine.from_path(waterfall_yaml)
        aspect = engine.get_aspect("modules")
        assert aspect is not None
        assert aspect.title == "Modules"

    def test_get_aspect_not_found(self, waterfall_yaml: Path):
        engine = MethodologyEngine.from_path(waterfall_yaml)
        assert engine.get_aspect("nonexistent") is None

    def test_list_aspect_names(self, waterfall_yaml: Path):
        engine = MethodologyEngine.from_path(waterfall_yaml)
        names = engine.list_aspect_names()
        assert "modules" in names
        assert "data_entities" in names

    def test_get_element_type_found(self, waterfall_yaml: Path):
        engine = MethodologyEngine.from_path(waterfall_yaml)
        et = engine.get_element_type("modules", "module")
        assert et is not None
        assert et.title == "Module"

    def test_get_element_type_not_found(self, waterfall_yaml: Path):
        engine = MethodologyEngine.from_path(waterfall_yaml)
        assert engine.get_element_type("modules", "nonexistent") is None

    def test_get_element_type_wrong_aspect(self, waterfall_yaml: Path):
        engine = MethodologyEngine.from_path(waterfall_yaml)
        assert engine.get_element_type("nonexistent", "module") is None

    def test_get_relationship_type_found(self, waterfall_yaml: Path):
        engine = MethodologyEngine.from_path(waterfall_yaml)
        rt = engine.get_relationship_type("references")
        assert rt is not None
        assert rt.title == "References"

    def test_get_relationship_type_not_found(self, waterfall_yaml: Path):
        engine = MethodologyEngine.from_path(waterfall_yaml)
        assert engine.get_relationship_type("nonexistent") is None

    def test_get_hierarchy(self, waterfall_yaml: Path):
        engine = MethodologyEngine.from_path(waterfall_yaml)
        hierarchy = engine.get_hierarchy("modules")
        assert hierarchy["module"] is None
        assert hierarchy["component"] == "module"

    def test_get_hierarchy_unknown_aspect(self, waterfall_yaml: Path):
        engine = MethodologyEngine.from_path(waterfall_yaml)
        assert engine.get_hierarchy("unknown") == {}


# ======================================================================
# Format
# ======================================================================


class TestMethodologyEngineFormat:
    def test_format_contains_name(self, minimal_yaml: Path):
        engine = MethodologyEngine.from_path(minimal_yaml)
        text = engine.format()
        assert "minimal" in text

    def test_format_contains_aspects(self, minimal_yaml: Path):
        engine = MethodologyEngine.from_path(minimal_yaml)
        text = engine.format()
        assert "features" in text
        assert "Feature" in text


# ======================================================================
# Validation
# ======================================================================


class TestMethodologyEngineValidation:
    def test_validate_with_empty_storage(self, waterfall_yaml: Path, tmp_path: Path):
        """Validation on an empty storage should pass (no elements to check)."""
        from src.storage.filesystem import FilesystemStorage

        storage = FilesystemStorage(tmp_path)
        engine = MethodologyEngine.from_path(waterfall_yaml)
        report = engine.validate(storage, fix=False)
        assert report.passed is True

    def test_validate_with_valid_element(self, waterfall_yaml: Path, tmp_path: Path):
        from src.storage.filesystem import FilesystemStorage

        storage = FilesystemStorage(tmp_path)
        storage.write_element(
            Element(
                id="MOD-001",
                aspect="modules",
                element_type="module",
                title="Test Module",
                status=ElementStatus.DRAFT,
            )
        )
        engine = MethodologyEngine.from_path(waterfall_yaml)
        report = engine.validate(storage, fix=False)
        assert report.passed is True

    def test_validate_with_broken_reference(self, waterfall_yaml: Path, tmp_path: Path):
        """Element references a nonexistent parent."""
        from src.storage.filesystem import FilesystemStorage

        storage = FilesystemStorage(tmp_path)
        storage.write_element(
            Element(
                id="MOD-001",
                aspect="modules",
                element_type="module",
                title="Module A",
                status=ElementStatus.DRAFT,
                parent="NONEXISTENT",
            )
        )
        engine = MethodologyEngine.from_path(waterfall_yaml)
        report = engine.validate(storage, fix=True)
        assert report.fixed > 0

    def test_validate_detects_unknown_aspect(self, waterfall_yaml: Path, tmp_path: Path):
        from src.storage.filesystem import FilesystemStorage

        storage = FilesystemStorage(tmp_path)
        storage.write_element(
            Element(
                id="MOD-001",
                aspect="unknown_aspect",
                element_type="module",
                title="Bad Element",
                status=ElementStatus.DRAFT,
            )
        )
        engine = MethodologyEngine.from_path(waterfall_yaml)
        report = engine.validate(storage, fix=False)
        assert report.passed is False

    def test_validate_detects_unknown_element_type(
        self, waterfall_yaml: Path, tmp_path: Path
    ):
        from src.storage.filesystem import FilesystemStorage

        storage = FilesystemStorage(tmp_path)
        storage.write_element(
            Element(
                id="MOD-001",
                aspect="modules",
                element_type="nonexistent_type",
                title="Bad Element",
                status=ElementStatus.DRAFT,
            )
        )
        engine = MethodologyEngine.from_path(waterfall_yaml)
        report = engine.validate(storage, fix=False)
        assert report.passed is False
        assert any("nonexistent_type" in e.message for e in report.errors)


# ======================================================================
# Metrics
# ======================================================================


class TestMethodologyEngineMetrics:
    def test_metrics_empty_storage(self, waterfall_yaml: Path, tmp_path: Path):
        from src.storage.filesystem import FilesystemStorage

        storage = FilesystemStorage(tmp_path)
        engine = MethodologyEngine.from_path(waterfall_yaml)
        metrics = engine.compute_metrics(storage)
        assert metrics.total_elements == 0
        assert metrics.connectivity_index == 0.0

    def test_metrics_with_elements(self, waterfall_yaml: Path, tmp_path: Path):
        from src.storage.filesystem import FilesystemStorage

        storage = FilesystemStorage(tmp_path)
        storage.write_element(
            Element(
                id="MOD-001",
                aspect="modules",
                element_type="module",
                title="Module A",
                status=ElementStatus.DRAFT,
            )
        )
        storage.write_element(
            Element(
                id="MOD-002",
                aspect="modules",
                element_type="component",
                title="Component B",
                status=ElementStatus.REVIEWED,
                parent="MOD-001",
            )
        )
        engine = MethodologyEngine.from_path(waterfall_yaml)
        metrics = engine.compute_metrics(storage)
        assert metrics.total_elements == 2
        assert metrics.total_relationships >= 1
        assert metrics.aspects.get("modules", 0) == 2

    def test_metrics_valid_types(self, waterfall_yaml: Path, tmp_path: Path):
        """Ensure metrics returns correct types for all fields."""
        from src.storage.filesystem import FilesystemStorage

        storage = FilesystemStorage(tmp_path)
        engine = MethodologyEngine.from_path(waterfall_yaml)
        metrics = engine.compute_metrics(storage)
        assert isinstance(metrics.total_elements, int)
        assert isinstance(metrics.connectivity_index, float)
        assert isinstance(metrics.orphan_elements, int)
        assert isinstance(metrics.coverage_ratio, float)
        assert isinstance(metrics.aspects, dict)
        assert isinstance(metrics.by_status, dict)

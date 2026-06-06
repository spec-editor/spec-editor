"""Tests for methodology management: loading, validation, listing."""

from pathlib import Path

import pytest
import yaml

from src.config.methodology import (
    Methodology,
    MethodologyManager,
    get_aspect,
    get_element_type,
    load_methodology,
)

# ======================================================================
# Data: sample methodologies for testing
# ======================================================================


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
            "relationship_types": [],
        }
    ],
    "skills": [],
}


AGILE_METHODOLOGY = {
    "name": "agile",
    "version": "1.0",
    "description": "Agile/Scrum backlog methodology: epics, user stories, acceptance criteria",
    "aspects": [
        {
            "name": "backlog",
            "title": "Sprint Backlog",
            "description": "Backlog hierarchy: epic → user_story → acceptance_criteria",
            "element_types": [
                {
                    "name": "epic",
                    "title": "Epic",
                    "attributes": [
                        {
                            "name": "business_value",
                            "type": "string",
                            "title": "Business value",
                        },
                    ],
                },
                {
                    "name": "user_story",
                    "title": "User Story",
                    "attributes": [
                        {"name": "as_a", "type": "string", "title": "As a (role)"},
                        {"name": "i_want", "type": "string", "title": "I want (goal)"},
                        {
                            "name": "so_that",
                            "type": "string",
                            "title": "So that (benefit)",
                        },
                        {
                            "name": "story_points",
                            "type": "number",
                            "title": "Story points",
                        },
                        {
                            "name": "priority",
                            "type": "string",
                            "title": "Priority (P0-P4)",
                        },
                    ],
                },
                {
                    "name": "acceptance_criteria",
                    "title": "Acceptance Criterion",
                    "attributes": [
                        {
                            "name": "given",
                            "type": "string",
                            "title": "Given (precondition)",
                        },
                        {"name": "when", "type": "string", "title": "When (action)"},
                        {
                            "name": "then",
                            "type": "string",
                            "title": "Then (expected result)",
                        },
                    ],
                },
            ],
            "relationship_types": [
                {
                    "name": "consists_of",
                    "title": "Epic consists of user stories",
                    "source_aspects": ["backlog"],
                    "target_aspects": ["backlog"],
                    "cardinality": "1-to-many",
                },
                {
                    "name": "verified_by",
                    "title": "User story verified by acceptance criteria",
                    "source_aspects": ["backlog"],
                    "target_aspects": ["backlog"],
                    "cardinality": "1-to-many",
                },
            ],
        }
    ],
    "skills": ["product_owner", "scrum_master"],
}


API_FIRST_METHODOLOGY = {
    "name": "api_first",
    "version": "1.0",
    "description": "API-First contract development: OpenAPI-oriented methodology",
    "aspects": [
        {
            "name": "api",
            "title": "API Design",
            "description": "Service → endpoint → schema hierarchy",
            "element_types": [
                {
                    "name": "service",
                    "title": "API Service",
                    "attributes": [
                        {"name": "base_url", "type": "string", "title": "Base URL"},
                        {"name": "version", "type": "string", "title": "API version"},
                    ],
                },
                {
                    "name": "endpoint",
                    "title": "API Endpoint",
                    "attributes": [
                        {"name": "method", "type": "string", "title": "HTTP method"},
                        {"name": "path", "type": "string", "title": "URL path"},
                        {"name": "summary", "type": "string", "title": "Summary"},
                    ],
                },
                {
                    "name": "schema",
                    "title": "Data Schema",
                    "attributes": [
                        {
                            "name": "schema_type",
                            "type": "string",
                            "title": "Schema type (object, array)",
                        },
                    ],
                },
                {
                    "name": "auth_scheme",
                    "title": "Authentication Scheme",
                    "attributes": [
                        {
                            "name": "auth_type",
                            "type": "string",
                            "title": "Type (bearer, oauth2, apikey)",
                        },
                        {
                            "name": "scopes",
                            "type": "string",
                            "title": "Required scopes",
                        },
                    ],
                },
            ],
            "relationship_types": [
                {
                    "name": "exposes",
                    "title": "Service exposes endpoint",
                    "source_aspects": ["api"],
                    "target_aspects": ["api"],
                    "cardinality": "1-to-many",
                },
                {
                    "name": "uses_schema",
                    "title": "Endpoint uses schema",
                    "source_aspects": ["api"],
                    "target_aspects": ["api"],
                    "cardinality": "many-to-many",
                },
                {
                    "name": "secured_by",
                    "title": "Endpoint secured by auth scheme",
                    "source_aspects": ["api"],
                    "target_aspects": ["api"],
                    "cardinality": "many-to-many",
                },
            ],
        }
    ],
    "skills": ["api_designer", "backend_developer"],
}


# ======================================================================
# Tests
# ======================================================================


class TestMethodologyLoader:
    """Loading and validating methodology YAML files."""

    def test_load_minimal_methodology(self, tmp_path):
        path = tmp_path / "minimal.yaml"
        _write_yaml(path, MINIMAL_METHODOLOGY)

        m = load_methodology(path)
        assert m.name == "minimal"
        assert m.version == "1.0"
        assert len(m.aspects) == 1

    def test_load_agile_methodology(self, tmp_path):
        path = tmp_path / "agile.yaml"
        _write_yaml(path, AGILE_METHODOLOGY)

        m = load_methodology(path)
        assert m.name == "agile"
        assert len(m.aspects) == 1
        backlog = m.aspects[0]
        assert backlog.name == "backlog"

        # Element types
        et_names = [et.name for et in backlog.element_types]
        assert "epic" in et_names
        assert "user_story" in et_names
        assert "acceptance_criteria" in et_names

    def test_load_api_first_methodology(self, tmp_path):
        path = tmp_path / "api_first.yaml"
        _write_yaml(path, API_FIRST_METHODOLOGY)

        m = load_methodology(path)
        assert m.name == "api_first"
        api = m.aspects[0]
        assert api.name == "api"

        et_names = [et.name for et in api.element_types]
        assert "service" in et_names
        assert "endpoint" in et_names
        assert "schema" in et_names
        assert "auth_scheme" in et_names

    def test_load_nonexistent_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_methodology(Path("/nonexistent/methodology.yaml"))

    def test_load_empty_file_raises(self, tmp_path):
        path = tmp_path / "empty.yaml"
        path.write_text("")
        with pytest.raises(ValueError):
            load_methodology(path)

    def test_waterfall_methodology_exists(self):
        """Verify the bundled waterfall methodology loads correctly."""
        waterfall_path = (
            Path(__file__).parent.parent / "methodologies" / "waterfall.yaml"
        )
        m = load_methodology(waterfall_path)
        assert m.name == "waterfall"
        assert len(m.aspects) == 8  # sources, modules, user_scenarios, user_interface, data_entities, non_functional, implementation, metrics


class TestMethodologyQueries:
    """Querying aspects and element types from a methodology."""

    def test_get_aspect_found(self, tmp_path):
        path = tmp_path / "agile.yaml"
        _write_yaml(path, AGILE_METHODOLOGY)
        m = load_methodology(path)

        aspect = get_aspect(m, "backlog")
        assert aspect is not None
        assert aspect.title == "Sprint Backlog"

    def test_get_aspect_not_found(self, tmp_path):
        path = tmp_path / "agile.yaml"
        _write_yaml(path, AGILE_METHODOLOGY)
        m = load_methodology(path)

        assert get_aspect(m, "nonexistent") is None

    def test_get_element_type_found(self, tmp_path):
        path = tmp_path / "api_first.yaml"
        _write_yaml(path, API_FIRST_METHODOLOGY)
        m = load_methodology(path)

        et = get_element_type(m, "api", "endpoint")
        assert et is not None
        assert et.title == "API Endpoint"

    def test_get_element_type_wrong_aspect(self, tmp_path):
        path = tmp_path / "api_first.yaml"
        _write_yaml(path, API_FIRST_METHODOLOGY)
        m = load_methodology(path)

        assert get_element_type(m, "nonexistent", "endpoint") is None

    def test_agile_user_story_has_attributes(self, tmp_path):
        path = tmp_path / "agile.yaml"
        _write_yaml(path, AGILE_METHODOLOGY)
        m = load_methodology(path)

        story = get_element_type(m, "backlog", "user_story")
        assert story is not None
        attr_names = [a.name for a in story.attributes]
        assert "as_a" in attr_names
        assert "i_want" in attr_names
        assert "so_that" in attr_names
        assert "story_points" in attr_names

    def test_api_first_relationships(self, tmp_path):
        path = tmp_path / "api_first.yaml"
        _write_yaml(path, API_FIRST_METHODOLOGY)
        m = load_methodology(path)

        api = get_aspect(m, "api")
        assert api is not None
        rel_names = [rt.name for rt in api.relationship_types]
        assert "exposes" in rel_names
        assert "uses_schema" in rel_names
        assert "secured_by" in rel_names


class TestMethodologyValidation:
    """Validation of methodology YAML structure."""

    def test_methodology_requires_name(self):
        bad = dict(MINIMAL_METHODOLOGY)
        del bad["name"]
        with pytest.raises(ValueError):
            Methodology(**bad)

    def test_methodology_defaults_aspects(self):
        """Missing 'aspects' defaults to empty list (Pydantic default_factory)."""
        bad = dict(MINIMAL_METHODOLOGY)
        del bad["aspects"]
        m = Methodology(**bad)
        assert m.aspects == []

    def test_aspect_requires_name(self):
        bad = dict(MINIMAL_METHODOLOGY)
        bad["aspects"] = [{"title": "No Name"}]
        with pytest.raises(ValueError):
            Methodology(**bad)

    def test_methodology_defaults_are_sensible(self):
        m = Methodology(name="test", aspects=[])
        assert m.version == "1.0"
        assert m.description == ""
        assert m.skills == []


# ======================================================================
# MethodologyManager tests
# ======================================================================


class TestMethodologyManager:
    """Discovering and loading methodologies via the manager."""

    def test_list_available_real(self):
        """Real methodologies directory contains waterfall only (paid ones moved out)."""
        mgr = MethodologyManager()
        names = mgr.list_available()
        assert "waterfall" in names
        # paid methodologies moved to ../paid-methodologies/
        # they are not in the OSS repo

    def test_list_available_custom_dir(self, tmp_path):
        _write_yaml(tmp_path / "custom.yaml", MINIMAL_METHODOLOGY)
        mgr = MethodologyManager(methodologies_dir=tmp_path)
        assert mgr.list_available() == ["custom"]

    def test_list_available_empty_dir(self, tmp_path):
        mgr = MethodologyManager(methodologies_dir=tmp_path)
        assert mgr.list_available() == []

    def test_find_existing(self):
        mgr = MethodologyManager()
        path = mgr.find("waterfall")
        assert path is not None
        assert path.name in ("waterfall.yaml", "methodology.yaml")

    def test_find_nonexistent(self):
        mgr = MethodologyManager()
        assert mgr.find("nonexistent") is None

    def test_load_waterfall(self):
        mgr = MethodologyManager()
        m = mgr.load("waterfall")
        assert m.name == "waterfall"
        assert len(m.aspects) == 8  # sources, modules, user_scenarios, user_interface, data_entities, non_functional, implementation, metrics

    def test_load_agile(self, tmp_path):
        """Load agile from test data (paid, moved out of OSS repo)."""
        _write_yaml(tmp_path / "agile.yaml", AGILE_METHODOLOGY)
        mgr = MethodologyManager(methodologies_dir=tmp_path)
        m = mgr.load("agile")
        assert m.name == "agile"
        assert len(m.aspects) == 1
        assert m.aspects[0].name == "backlog"

    def test_load_api_first(self, tmp_path):
        """Load api_first from test data (paid, moved out of OSS repo)."""
        _write_yaml(tmp_path / "api_first.yaml", API_FIRST_METHODOLOGY)
        mgr = MethodologyManager(methodologies_dir=tmp_path)
        m = mgr.load("api_first")
        assert m.name == "api_first"
        assert len(m.aspects) == 1
        assert m.aspects[0].name == "api"

    def test_load_nonexistent_raises(self):
        mgr = MethodologyManager()
        with pytest.raises(FileNotFoundError, match="Methodology"):
            mgr.load("nonexistent")

    def test_get_default(self):
        mgr = MethodologyManager()
        m = mgr.get_default()
        assert m.name == "waterfall"
        assert len(m.aspects) == 8  # sources, modules, user_scenarios, user_interface, data_entities, non_functional, implementation, metrics

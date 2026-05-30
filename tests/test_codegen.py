"""Tests for spec-driven code generation engine."""

import tempfile
from pathlib import Path

from src.codegen.engine import CodeGenerator
from src.storage.models import Element, ElementStatus


def _el(id_: str, title: str, element_type: str, content: str = "", **kw) -> Element:
    defaults = {
        "aspect": "modules",
        "status": ElementStatus.CONFIRMED,
        "parent": None,
        "children": [],
        "relationships": {},
        "tags": [],
        "provenance": None,
        "derived_from": [],
        "covered_by": [],
    }
    defaults.update(kw)
    return Element(
        id=id_, title=title, element_type=element_type, content=content, **defaults
    )


class TestTemplateRendering:
    def test_entity_to_sqlalchemy_model(self):
        el = _el(
            "ENT-001", "User", "entity", content="User account with email and name"
        )
        gen = CodeGenerator()

        result = gen.render_element(el, "python_sqlalchemy_model.py.j2")
        assert "class User(Base):" in result
        assert "pass" in result
        assert '@implements("ENT-001")' in result
        assert "User account with email and name" in result

    def test_entity_to_typescript_interface(self):
        el = _el("ENT-002", "Product", "entity", content="Product with price and SKU")
        gen = CodeGenerator()

        result = gen.render_element(el, "typescript_interface.ts.j2")
        assert "interface Product" in result
        assert '@implements("ENT-002")' in result

    def test_api_endpoint_to_fastapi_router(self):
        el = _el(
            "API-001",
            "Create User",
            "api_endpoint",
            content="POST /users — create a new user account",
        )
        gen = CodeGenerator()

        result = gen.render_element(el, "python_fastapi_router.py.j2")
        assert "router" in result.lower()
        assert '@implements("API-001")' in result

    def test_test_case_to_pytest(self):
        el = _el(
            "TST-001",
            "User Creation Test",
            "test_case",
            content="Should return 201 when creating a valid user",
        )
        gen = CodeGenerator()

        result = gen.render_element(el, "python_pytest.py.j2")
        assert "def test_" in result
        assert '@implements("TST-001")' in result

    def test_component_to_react_tsx(self):
        el = _el(
            "CMP-001",
            "UserProfile",
            "component",
            content="User profile card with avatar, name, and email",
        )
        gen = CodeGenerator()

        result = gen.render_element(el, "react_component.tsx.j2")
        assert "const UserProfile" in result
        assert '@implements("CMP-001")' in result
        assert "export default UserProfile" in result
        assert "User profile card" in result

    def test_component_to_react_tsx_with_props(self):
        el = _el(
            "CMP-002",
            "SearchBar",
            "component",
            content="Search input with onSearch callback",
        )
        gen = CodeGenerator()

        result = gen.render_element(el, "react_component.tsx.j2")
        assert "interface SearchBarProps" in result
        assert "const SearchBar" in result
        assert "React.FC<SearchBarProps>" in result

    def test_unknown_template_returns_empty(self):
        el = _el("MOD-001", "Auth", "module")
        gen = CodeGenerator()

        result = gen.render_element(el, "nonexistent_template")
        assert result == ""

    def test_rendered_code_has_no_trailing_whitespace_lines(self):
        el = _el("ENT-001", "User", "entity")
        gen = CodeGenerator()

        result = gen.render_element(el, "python_sqlalchemy_model.py.j2")
        lines = result.split("\n")
        trailing_empty = 0
        for line in reversed(lines):
            if line.strip() == "":
                trailing_empty += 1
            else:
                break
        assert trailing_empty <= 1

    def test_user_story_to_pytest(self):
        """User story with Given/When/Then generates structured tests."""
        el = _el(
            "US-001",
            "User Login",
            "user_story",
            content=(
                "Given a registered user with valid credentials\n"
                "When the user submits the login form\n"
                "Then the user is redirected to the dashboard"
            ),
        )
        gen = CodeGenerator()

        result = gen.render_element(el, "python_pytest_user_story.py.j2")
        assert "class TestUserLogin" in result
        assert '@implements("US-001")' in result
        assert "def test_user_login_scenario" in result
        assert "def test_user_login_acceptance" in result
        assert "registered user with valid credentials" in result
        assert "redirected to the dashboard" in result

    def test_user_story_without_gwt_generates_skeleton(self):
        """User story without Given/When/Then still generates basic test."""
        el = _el(
            "US-002",
            "View Profile",
            "user_story",
            content="User should be able to view their profile page",
        )
        gen = CodeGenerator()

        result = gen.render_element(el, "python_pytest_user_story.py.j2")
        assert "class TestViewProfile" in result
        assert "def test_view_profile_scenario" in result
        # No explicit GWT → no acceptance test generated
        assert "def test_view_profile_acceptance" not in result
        assert "def test_view_profile_edge_cases" in result


class TestTemplateMapping:
    def test_default_mapping(self):
        gen = CodeGenerator()
        assert gen.get_template("entity") is not None
        assert gen.get_template("api_endpoint") is not None
        assert gen.get_template("test_case") is not None
        assert gen.get_template("component") is not None

    def test_custom_mapping(self):
        custom = {
            "mappings": {
                "entity": {"template": "custom_entity.py.j2"},
            }
        }
        gen = CodeGenerator(config=custom)
        assert gen.get_template("entity") == "custom_entity.py.j2"

    def test_unmapped_type_returns_none(self):
        gen = CodeGenerator()
        assert gen.get_template("nonexistent_type") is None


class TestDirectoryOutput:
    def test_generate_to_directory(self):
        el = _el("ENT-001", "User", "entity", content="User entity")
        gen = CodeGenerator()

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            result = gen.generate_element(el, output_dir)
            assert result["status"] == "created"
            assert result["file"].endswith("user.py")
            content = Path(result["file"]).read_text()
            assert '@implements("ENT-001")' in content

    def test_dry_run_does_not_write(self):
        el = _el("ENT-001", "User", "entity")
        gen = CodeGenerator()

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            result = gen.generate_element(el, output_dir, dry_run=True)
            assert result["status"] == "dry_run"
            assert not any(output_dir.iterdir())

    def test_multiple_elements_to_directory(self):
        elements = [
            _el("ENT-001", "User", "entity"),
            _el("ENT-002", "Product", "entity"),
        ]
        gen = CodeGenerator()

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            results = gen.generate_all(elements, output_dir)
            assert len(results) == 2
            assert all(r["status"] == "created" for r in results)
            files = list(output_dir.glob("*.py"))
            assert len(files) == 2

    def test_skip_unmapped_element_types(self):
        elements = [
            _el("MOD-001", "Auth Module", "module"),
            _el("ENT-001", "User", "entity"),
        ]
        gen = CodeGenerator()

        with tempfile.TemporaryDirectory() as tmp:
            results = gen.generate_all(elements, Path(tmp))
            assert len(results) == 2
            skipped = [r for r in results if r["status"] == "skipped"]
            created = [r for r in results if r["status"] == "created"]
            assert len(skipped) == 1
            assert len(created) == 1

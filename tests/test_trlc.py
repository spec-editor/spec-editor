"""Tests for TRLC export format.

TRLC (Treat Requirements Like Code, BMW) is a DSL for requirements
with types, references, and static analysis. We generate .trlc files
from our specification elements.
"""

from pathlib import Path

from src.export.trlc import (
    TRLCExporter,
    element_to_trlc,
    trlc_escape,
)
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


class TestTRLCEscape:
    """String escaping for TRLC format."""

    def test_simple_string(self):
        assert trlc_escape("Hello World") == "Hello World"

    def test_quotes_escaped(self):
        assert trlc_escape('Quote "test"') == 'Quote \\"test\\"'

    def test_newlines_escaped(self):
        assert trlc_escape("Line1\nLine2") == "Line1\\nLine2"

    def test_backslash_escaped(self):
        assert trlc_escape(r"path\to\file") == "path\\\\to\\\\file"

    def test_empty_string(self):
        assert trlc_escape("") == ""


class TestElementToTRLC:
    """Converting individual elements to TRLC format."""

    def test_module_element(self):
        el = _el(
            "MOD-001",
            "Auth Module",
            "module",
            content="Handles authentication and authorisation",
        )
        result = element_to_trlc(el)
        assert "MOD-001" in result
        assert "Auth Module" in result
        assert "Handles authentication" in result

    def test_entity_element(self):
        el = _el(
            "ENT-001", "User", "entity", content="User account with email and password"
        )
        result = element_to_trlc(el)
        assert "ENT-001" in result
        assert '"User"' in result
        assert "entity ENT-001" in result

    def test_requirement_with_parent(self):
        el = _el("REQ-042", "Login Page", "requirement", parent="MOD-001")
        result = element_to_trlc(el)
        assert "parent = MOD-001" in result

    def test_requirement_with_children(self):
        el = _el("MOD-001", "Auth", "module", children=["MOD-002", "MOD-003"])
        result = element_to_trlc(el)
        assert "children =" in result
        assert "MOD-002" in result
        assert "MOD-003" in result

    def test_requirement_with_tags(self):
        el = _el("REQ-001", "Security", "requirement", tags=["security", "p0"])
        result = element_to_trlc(el)
        assert 'tags = ["security", "p0"]' in result

    def test_requirement_with_derived_from(self):
        el = _el(
            "SPEC-001",
            "Derived Spec",
            "requirement",
            derived_from=["SRC-001", "SRC-002"],
        )
        result = element_to_trlc(el)
        assert "derived_from = " in result
        assert "SRC-001" in result


class TestTRLCExporter:
    """Full TRLC export from multiple elements."""

    def test_export_multiple_elements(self, tmp_path):
        elements = [
            _el("MOD-001", "Auth Module", "module", content="Authentication"),
            _el("MOD-002", "Payment Module", "module", content="Payment processing"),
            _el("ENT-001", "User", "entity", content="User model"),
        ]

        exporter = TRLCExporter()
        output_path = tmp_path / "requirements.trlc"
        exporter.export(elements, output_path)

        content = output_path.read_text()
        assert "MOD-001" in content
        assert "MOD-002" in content
        assert "ENT-001" in content
        # Elements are separated by blank lines
        assert "\n\n" in content

    def test_export_empty_list(self, tmp_path):
        exporter = TRLCExporter()
        output_path = tmp_path / "empty.trlc"
        exporter.export([], output_path)
        # Should create an empty file or file with a comment
        assert output_path.exists()

    def test_export_header_included(self, tmp_path):
        elements = [_el("REQ-001", "Test", "requirement")]
        exporter = TRLCExporter()
        output_path = tmp_path / "header.trlc"
        exporter.export(elements, output_path)

        content = output_path.read_text()
        # TRLC files typically start with a package declaration or comment
        assert content.strip() != ""

    def test_content_has_no_unescaped_quotes(self, tmp_path):
        el = _el(
            "REQ-001",
            'Module with "quotes"',
            "module",
            content='Handles "special" characters',
        )
        exporter = TRLCExporter()
        output_path = tmp_path / "escaped.trlc"
        exporter.export(elements=[el], output_path=output_path)

        content = output_path.read_text()
        # Quotes inside strings should be escaped
        assert '\\"' in content

    def test_trlc_type_mapping(self, tmp_path):
        """Different element types map to appropriate TRLC types."""
        elements = [
            _el("MOD-001", "Auth", "module"),
            _el("ENT-001", "User", "entity"),
            _el("REQ-001", "Security", "requirement"),
            _el("API-001", "Get Users", "api_endpoint"),
        ]
        exporter = TRLCExporter()
        output_path = tmp_path / "types.trlc"
        exporter.export(elements, output_path)
        content = output_path.read_text()
        # Each element appears with its type and ID
        assert "module MOD-001" in content
        assert "entity ENT-001" in content
        assert "requirement REQ-001" in content
        assert "api_endpoint API-001" in content
        assert "Get Users" in content

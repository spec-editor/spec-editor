"""Tests for regulatory methodology and compliance XLSX export."""

import tempfile
from pathlib import Path

import yaml

from src.config.methodology import Methodology, load_methodology
from src.storage.models import Element, ElementStatus


def _el(id_: str, title: str, element_type: str, content: str = "", **kw) -> Element:
    defaults = {
        "aspect": "compliance",
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


# ======================================================================
# Regulatory methodology YAML
# ======================================================================


class TestRegulatoryMethodology:
    """The regulatory methodology defines compliance-aspect structure."""

    def test_loads_without_errors(self):
        """Bundled regulatory.yaml is valid YAML and Pydantic model."""
        path = Path(__file__).parent.parent / "methodologies" / "regulatory.yaml"
        if not path.exists():
            path = (
                Path(__file__).parent.parent.parent
                / "paid-methodologies"
                / "regulatory.yaml"
            )
        if not path.exists():
            import pytest

            pytest.skip("regulatory.yaml not found (may be in paid-methodologies)")

        m = load_methodology(path)
        assert m.name == "regulatory"

    def test_has_compliance_aspect(self):
        """The core aspect is 'compliance'."""
        m = Methodology(
            name="regulatory",
            aspects=[
                {
                    "name": "compliance",
                    "title": "Compliance",
                    "element_types": [
                        {"name": "regulation", "title": "Regulation"},
                        {"name": "control", "title": "Control"},
                        {"name": "evidence", "title": "Evidence"},
                    ],
                }
            ],
        )
        aspect = m.aspects[0]
        assert aspect.name == "compliance"
        et_names = [et.name for et in aspect.element_types]
        assert "regulation" in et_names
        assert "control" in et_names
        assert "evidence" in et_names

    def test_regulation_has_article_attribute(self):
        """Regulation elements reference specific legal articles."""
        m = Methodology(
            name="regulatory",
            aspects=[
                {
                    "name": "compliance",
                    "title": "Compliance",
                    "element_types": [
                        {
                            "name": "regulation",
                            "title": "Regulation",
                            "attributes": [
                                {
                                    "name": "article",
                                    "type": "string",
                                    "title": "Legal article reference",
                                },
                                {
                                    "name": "jurisdiction",
                                    "type": "string",
                                    "title": "Applicable jurisdiction",
                                },
                            ],
                        },
                        {
                            "name": "control",
                            "title": "Control",
                            "attributes": [
                                {
                                    "name": "control_type",
                                    "type": "string",
                                    "title": "Type: preventive, detective, corrective",
                                },
                                {
                                    "name": "implementation_status",
                                    "type": "string",
                                    "title": "Status: planned, implemented, verified",
                                },
                            ],
                        },
                        {
                            "name": "evidence",
                            "title": "Evidence",
                            "attributes": [
                                {
                                    "name": "evidence_type",
                                    "type": "string",
                                    "title": "Type: document, log, attestation, test_result",
                                },
                                {
                                    "name": "retention_period",
                                    "type": "string",
                                    "title": "How long to keep",
                                },
                            ],
                        },
                    ],
                }
            ],
        )
        regulation_et = m.aspects[0].element_types[0]
        attr_names = [a.name for a in regulation_et.attributes]
        assert "article" in attr_names
        assert "jurisdiction" in attr_names

    def test_relationship_types(self):
        """Compliance has implements and evidenced_by relationships."""
        m = Methodology(
            name="regulatory",
            aspects=[
                {
                    "name": "compliance",
                    "title": "Compliance",
                    "element_types": [],
                    "relationship_types": [
                        {
                            "name": "implements",
                            "title": "Control implements regulation",
                            "source_aspects": ["compliance"],
                            "target_aspects": ["compliance"],
                            "cardinality": "many-to-many",
                        },
                        {
                            "name": "evidenced_by",
                            "title": "Control evidenced by",
                            "source_aspects": ["compliance"],
                            "target_aspects": ["compliance"],
                            "cardinality": "1-to-many",
                        },
                    ],
                }
            ],
        )
        rel_names = [rt.name for rt in m.aspects[0].relationship_types]
        assert "implements" in rel_names
        assert "evidenced_by" in rel_names


# ======================================================================
# Compliance XLSX export
# ======================================================================


class TestComplianceExporter:
    """XLSX export for compliance traceability matrix."""

    def _get_exporter(self):
        from src.export.compliance_exporter import ComplianceExporter

        return ComplianceExporter()

    def test_export_creates_file(self, tmp_path):
        """Export produces a valid .xlsx file."""
        exporter = self._get_exporter()

        regulations = [
            _el(
                "REG-001",
                "GDPR Article 5",
                "regulation",
                content="article: Art. 5(1)(c)\njurisdiction: EU",
            ),
        ]
        controls = [
            _el(
                "CTL-001",
                "Data Minimisation Check",
                "control",
                content="control_type: preventive\nimplementation_status: implemented",
            ),
        ]
        evidences = [
            _el(
                "EVD-001",
                "Data Audit Log",
                "evidence",
                content="evidence_type: document\nretention_period: 5 years",
            ),
        ]

        output = tmp_path / "compliance_matrix.xlsx"
        exporter.export(regulations, controls, evidences, output)
        assert output.exists()
        assert output.stat().st_size > 0

    def test_export_to_string(self):
        """Export to string returns a summary."""
        exporter = self._get_exporter()
        summary = exporter.export_to_summary(
            [_el("REG-001", "GDPR Art 5", "regulation")],
            [_el("CTL-001", "Minimisation", "control")],
            [_el("EVD-001", "Log", "evidence")],
        )
        assert "REG-001" in summary
        assert "CTL-001" in summary
        assert "EVD-001" in summary

    def test_empty_input_creates_header_only(self, tmp_path):
        """Empty lists produce a file with headers."""
        exporter = self._get_exporter()
        output = tmp_path / "empty.xlsx"
        exporter.export([], [], [], output)
        assert output.exists()

    def test_links_controls_to_regulations(self):
        """Controls linked via implements relationship appear under their regulation."""
        exporter = self._get_exporter()

        reg = _el("REG-001", "GDPR Art 5", "regulation")
        ctl = _el(
            "CTL-001",
            "Minimisation",
            "control",
            relationships={"implements": [{"role": "implements", "target": "REG-001"}]},
        )

        summary = exporter.export_to_summary([reg], [ctl], [])
        assert "REG-001" in summary
        assert "CTL-001" in summary

    def test_coverage_stats(self):
        """Compute coverage: controls with evidence / total controls."""
        exporter = self._get_exporter()
        stats = exporter.compute_coverage(
            [_el("CTL-001", "C1", "control"), _el("CTL-002", "C2", "control")],
            [_el("EVD-001", "E1", "evidence")],
        )
        # 1 evidence, 2 controls — but evidence links aren't tracked without relationships
        assert stats["total_controls"] == 2
        assert stats["total_evidence"] == 1
        assert 0 <= stats["coverage_ratio"] <= 1

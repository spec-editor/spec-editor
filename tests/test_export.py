"""Tests for spec-editor export (all formats).

Tests use the export pipeline directly to avoid circular imports
from the CLI command layer.
"""

import tempfile
from pathlib import Path

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_minimal_project(path: Path) -> None:
    """Create a minimal spec-editor project with a few elements."""
    (path / "methodology.yaml").write_text(
        """name: waterfall
version: "1.0"
aspects:
  - name: sources
    allowed_types: [source]
  - name: modules
    allowed_types: [module]
  - name: user_scenarios
    allowed_types: [high_level_scenario]
relationship_types:
  - name: derived_from
    cross_aspect: true
  - name: implements
    cross_aspect: true
  - name: consists_of
    cross_aspect: false
""",
        encoding="utf-8",
    )

    (path / "agents.yaml").write_text(
        "agents:\n  agent_1:\n    model: test\n  agent_2:\n    model: test\n",
        encoding="utf-8",
    )

    (path / "aspects" / "sources").mkdir(parents=True)
    (path / "aspects" / "sources" / "SRC-001.md").write_text(
        """---
aspect: sources
element_type: source
id: SRC-001
status: reviewed
title: Source Document
---
Requirements for a site management platform.
""",
        encoding="utf-8",
    )

    (path / "aspects" / "modules").mkdir(parents=True)
    (path / "aspects" / "modules" / "MOD-001.md").write_text(
        """---
aspect: modules
derived_from:
- SRC-001
element_type: module
id: MOD-001
relationships:
  derived_from:
  - role: derived_from
    target: SRC-001
status: reviewed
title: Site Manager Module
---
Core module for managing sites. Handles CRUD operations.
""",
        encoding="utf-8",
    )

    (path / "aspects" / "user_scenarios").mkdir(parents=True)
    (path / "aspects" / "user_scenarios" / "SCN-001.md").write_text(
        """---
aspect: user_scenarios
derived_from:
- SRC-001
element_type: high_level_scenario
id: SCN-001
relationships:
  derived_from:
  - role: derived_from
    target: SRC-001
status: reviewed
title: Create New Site
---
Operator selects a domain and creates a new site project.
""",
        encoding="utf-8",
    )


# ── Export tests ───────────────────────────────────────────────────────────


class TestExportSRS:
    """SRS format (IEEE 830 Markdown) via pipeline."""

    def test_srs_export_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "project"
            proj.mkdir()
            _make_minimal_project(proj)

            from importlib import resources
            from src.export.pipeline import pipeline_from_config
            from src.storage.filesystem import FilesystemStorage

            storage = FilesystemStorage(proj)
            tpl = resources.files("data") / "srs_template.yaml"
            output = str(proj / "output.srs.md")

            pipeline = pipeline_from_config(
                {"gatherer": "srs", "formatter": "markdown", "transport": "file"},
                storage, proj,
            )
            _, _ = pipeline.run(
                storage, tpl, proj,
                transport_config={"output": output},
            )

            assert Path(output).exists()
            content = Path(output).read_text(encoding="utf-8")
            assert "Site Manager Module" in content
            assert "Create New Site" in content

    def test_srs_has_element_titles(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "project"
            proj.mkdir()
            _make_minimal_project(proj)

            from importlib import resources
            from src.export.pipeline import pipeline_from_config
            from src.storage.filesystem import FilesystemStorage

            storage = FilesystemStorage(proj)
            tpl = resources.files("data") / "srs_template.yaml"
            output = str(proj / "out.md")

            pipeline = pipeline_from_config(
                {"gatherer": "srs", "formatter": "markdown", "transport": "file"},
                storage, proj,
            )
            _, _ = pipeline.run(
                storage, tpl, proj,
                transport_config={"output": output},
            )

            content = Path(output).read_text(encoding="utf-8")
            # SRS uses titles, not IDs — check content is present
            assert "Site Manager Module" in content
            assert "Create New Site" in content
            assert "Software Requirements Specification" in content


class TestExportHTML:
    """HTML format via pipeline."""

    def test_html_export_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "project"
            proj.mkdir()
            _make_minimal_project(proj)

            from importlib import resources
            from src.export.pipeline import pipeline_from_config
            from src.storage.filesystem import FilesystemStorage

            storage = FilesystemStorage(proj)
            srs_tpl = resources.files("data") / "srs_template.yaml"
            html_tpl = resources.files("data") / "srs_style.j2"
            output = str(proj / "output.html")

            pipeline = pipeline_from_config(
                {"gatherer": "srs", "formatter": "jinja2", "transport": "file"},
                storage, proj,
            )
            _, _ = pipeline.run(
                storage, srs_tpl, proj,
                format_config={"template": str(html_tpl)},
                transport_config={"output": output},
            )

            assert Path(output).exists()
            content = Path(output).read_text(encoding="utf-8")
            assert "Site Manager" in content


class TestExportCLI:
    """CLI export command integration tests."""

    def test_export_default_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "project"
            proj.mkdir()
            _make_minimal_project(proj)

            from click.testing import CliRunner
            from src.cli.commands import cli

            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["export", "-p", str(proj), "-f", "srs", "-o", str(proj / "out.md")],
            )
            assert result.exit_code == 0
            assert (proj / "out.md").exists()

    def test_export_missing_project(self):
        from click.testing import CliRunner
        from src.cli.commands import cli

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["export", "-p", "/nonexistent/path/xyz", "-f", "srs"],
        )
        assert result.exit_code != 0

    def test_export_html_via_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "project"
            proj.mkdir()
            _make_minimal_project(proj)

            from click.testing import CliRunner
            from src.cli.commands import cli

            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["export", "-p", str(proj), "-f", "html", "-o", str(proj / "out.html")],
            )
            assert result.exit_code == 0
            assert (proj / "out.html").exists()


class TestExportEmptyProject:
    """Edge case: empty project with no elements."""

    def test_export_empty_project_no_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "empty"
            proj.mkdir()
            (proj / "methodology.yaml").write_text(
                "name: empty\nversion: '1.0'\naspects: []\nrelationship_types: []\n",
                encoding="utf-8",
            )
            (proj / "agents.yaml").write_text("agents:\n  agent_1:\n    model: test\n")
            (proj / "aspects").mkdir()

            from importlib import resources
            from src.export.pipeline import pipeline_from_config
            from src.storage.filesystem import FilesystemStorage

            storage = FilesystemStorage(proj)
            tpl = resources.files("data") / "srs_template.yaml"
            output = str(proj / "out.md")

            pipeline = pipeline_from_config(
                {"gatherer": "srs", "formatter": "markdown", "transport": "file"},
                storage, proj,
            )
            # Should not crash on empty project
            _, _ = pipeline.run(
                storage, tpl, proj,
                transport_config={"output": output},
            )
            assert Path(output).exists()

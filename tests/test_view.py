"""Tests for spec-editor view — renders spec graph as HTML/Mermaid."""

import tempfile
from pathlib import Path

import pytest

from src.view.renderer import MermaidRenderer


class TestMermaidRenderer:
    """Graph visualization via Mermaid.js."""

    @pytest.fixture
    def bookstore_project(self) -> Path:
        from importlib import resources

        return resources.files("data") / "examples" / "bookstore"

    @pytest.fixture
    def renderer(self) -> MermaidRenderer:
        return MermaidRenderer()

    # --- Mermaid diagram generation ---

    def test_renders_elements_as_nodes(
        self, renderer: MermaidRenderer, bookstore_project: Path
    ) -> None:
        """Each element becomes a Mermaid graph node."""
        mermaid = renderer.build_mermaid(bookstore_project)

        # Should have nodes for elements
        assert "MOD-001" in mermaid
        assert "ENT-001" in mermaid
        assert "NFR-001" in mermaid
        assert "SCN-001" in mermaid

    def test_renders_relationships_as_edges(
        self, renderer: MermaidRenderer, bookstore_project: Path
    ) -> None:
        """Relationships become graph edges."""
        mermaid = renderer.build_mermaid(bookstore_project)

        # MOD-001 relates_to MOD-004 -> should have edge
        assert "MOD-001" in mermaid
        assert "-->" in mermaid  # Mermaid edge syntax

    def test_graph_is_valid_mermaid(
        self, renderer: MermaidRenderer, bookstore_project: Path
    ) -> None:
        """Output starts with graph directive."""
        mermaid = renderer.build_mermaid(bookstore_project)
        lines = mermaid.strip().split("\n")
        assert lines[0].startswith("graph ")

    def test_nodes_have_labels(
        self, renderer: MermaidRenderer, bookstore_project: Path
    ) -> None:
        """Nodes include element titles as labels."""
        mermaid = renderer.build_mermaid(bookstore_project)
        assert "Book Catalog" in mermaid
        assert "Shopping Cart" in mermaid

    def test_empty_project(self, renderer: MermaidRenderer, tmp_path: Path) -> None:
        """Empty project produces minimal valid graph."""
        (tmp_path / "aspects").mkdir()
        mermaid = renderer.build_mermaid(tmp_path)
        assert "graph " in mermaid

    # --- HTML generation ---

    def test_generates_html_file(
        self, renderer: MermaidRenderer, bookstore_project: Path, tmp_path: Path
    ) -> None:
        """Writes self-contained HTML with Mermaid CDN."""
        output = tmp_path / "spec.html"
        path = renderer.render_html(bookstore_project, output)

        assert path.exists()
        content = path.read_text()
        assert "<!DOCTYPE html>" in content
        assert "mermaid" in content.lower()
        assert "MOD-001" in content

    def test_html_is_self_contained(
        self, renderer: MermaidRenderer, bookstore_project: Path, tmp_path: Path
    ) -> None:
        """HTML loads Mermaid from CDN, no local files needed."""
        output = tmp_path / "spec.html"
        path = renderer.render_html(bookstore_project, output)

        content = path.read_text()
        assert "cdn.jsdelivr.net" in content or "unpkg.com" in content

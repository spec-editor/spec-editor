"""Tests for ContextBuilder — spec context for AI coding assistants."""

from pathlib import Path

import pytest

from src.context.builder import ContextBuilder
from src.storage.filesystem import FilesystemStorage


class TestContextBuilder:
    """ContextBuilder: building context for AI coding agents."""

    @pytest.fixture
    def bookstore(self) -> Path:
        from importlib import resources

        return resources.files("data") / "examples" / "bookstore"

    @pytest.fixture
    def storage(self, bookstore: Path) -> FilesystemStorage:
        return FilesystemStorage(bookstore)

    @pytest.fixture
    def builder(self, storage: FilesystemStorage, bookstore: Path) -> ContextBuilder:
        return ContextBuilder(storage, bookstore)

    # --- File context ---

    def test_parses_implements_annotations(self, tmp_path: Path) -> None:
        """Extracts requirement IDs from @implements annotations."""
        f = tmp_path / "test.py"
        f.write_text('''"""
@implements("MOD-001")
@implements("SCN-003")
"""
class AuthService:
    pass
''')
        builder = ContextBuilder(FilesystemStorage(tmp_path))
        ids = builder._parse_implements(f)
        assert set(ids) == {"MOD-001", "SCN-003"}

    def test_parses_comment_style(self, tmp_path: Path) -> None:
        """Parses // @implements("ID") style."""
        f = tmp_path / "test.go"
        f.write_text('// @implements("MOD-001")\nfunc Login() {}')
        builder = ContextBuilder(FilesystemStorage(tmp_path))
        ids = builder._parse_implements(f)
        assert ids == ["MOD-001"]

    def test_file_without_implements(self, tmp_path: Path) -> None:
        """File without annotations returns empty."""
        f = tmp_path / "test.py"
        f.write_text("def foo(): pass")
        builder = ContextBuilder(FilesystemStorage(tmp_path))
        assert builder.for_file(f).startswith("No @implements")

    def test_context_for_nonexistent_file(self, tmp_path: Path) -> None:
        """Missing file returns empty list."""
        builder = ContextBuilder(FilesystemStorage(tmp_path))
        assert builder._parse_implements(Path("/nonexistent")) == []

    # --- Element context from bookstore ---

    def test_context_for_element(self, builder: ContextBuilder) -> None:
        """Builds context for a single element."""
        ctx = builder.for_element("MOD-001")
        assert "MOD-001" in ctx
        assert "Book Catalog" in ctx

    def test_context_includes_related(self, builder: ContextBuilder) -> None:
        """Context includes related elements (1-hop)."""
        ctx = builder.for_element("MOD-001")
        # MOD-001 relates_to MOD-004 -> should appear in context
        assert "Related Elements" in ctx or "MOD-004" in ctx

    def test_context_for_missing_element(self, builder: ContextBuilder) -> None:
        """Missing element returns error message."""
        ctx = builder.for_element("NONEXISTENT")
        assert "not found" in ctx.lower()

    # --- Task context ---

    def test_task_search(self, builder: ContextBuilder) -> None:
        """Searches spec for task keywords."""
        ctx = builder.for_task("checkout payment")
        assert "Checkout" in ctx or "Payment" in ctx or "MOD-003" in ctx

    def test_task_no_results(self, builder: ContextBuilder) -> None:
        """No matching elements returns message."""
        ctx = builder.for_task("xyzzy_nonexistent_query")
        assert "No spec elements found" in ctx

    # --- Context structure ---

    def test_context_has_header(self, builder: ContextBuilder) -> None:
        """Context starts with Spec Editor header."""
        ctx = builder.for_element("MOD-001")
        assert ctx.startswith("## Spec Editor Context")

    def test_context_is_markdown(self, builder: ContextBuilder) -> None:
        """Context is valid Markdown with headers."""
        ctx = builder.for_element("MOD-001")
        assert "## " in ctx
        assert "**" in ctx


class TestContextBuilderEmpty:
    """Behaviour with empty storage."""

    @pytest.fixture
    def builder(self, tmp_path: Path) -> ContextBuilder:
        (tmp_path / "aspects").mkdir()
        return ContextBuilder(FilesystemStorage(tmp_path))

    def test_empty_storage(self, builder: ContextBuilder) -> None:
        """Empty storage returns not-found messages."""
        assert "not found" in builder.for_element("X").lower()
        assert "No spec elements" in builder.for_task("test")


class TestSmartContext:
    """Pro features: hierarchical sub-graph + token budget."""

    @pytest.fixture
    def bookstore(self) -> Path:
        from importlib import resources

        return resources.files("data") / "examples" / "bookstore"

    @pytest.fixture
    def builder(self, bookstore: Path) -> ContextBuilder:
        return ContextBuilder(FilesystemStorage(bookstore), bookstore)

    def test_smart_context_depth_0(self, builder: ContextBuilder) -> None:
        """Depth 0 returns only primary elements."""
        ctx = builder.smart_context(["MOD-001"], depth=0)
        assert "MOD-001" in ctx
        assert "Book Catalog" in ctx

    def test_smart_context_depth_1(self, builder: ContextBuilder) -> None:
        """Depth 1 includes direct neighbours."""
        ctx = builder.smart_context(["MOD-001"], depth=1)
        # MOD-001 relates_to MOD-004
        assert "MOD-004" in ctx or "Related" in ctx

    def test_smart_context_shows_layer_labels(self, builder: ContextBuilder) -> None:
        """Output labels layers: Primary, Directly Related."""
        ctx = builder.smart_context(["MOD-001"], depth=1)
        assert "Primary" in ctx

    def test_smart_context_empty(self, builder: ContextBuilder) -> None:
        """Non-existent IDs return message."""
        assert "No spec elements" in builder.smart_context(["NONEXISTENT"])

    def test_context_with_budget_fits(self, builder: ContextBuilder) -> None:
        """Large budget returns full context."""
        ctx = builder.context_with_budget(["MOD-001"], token_budget=10000)
        assert "MOD-001" in ctx

    def test_context_with_budget_reduces(self, builder: ContextBuilder) -> None:
        """Small budget returns reduced context."""
        full = builder.smart_context(["MOD-001"], depth=2)
        reduced = builder.context_with_budget(["MOD-001"], token_budget=200)
        assert len(reduced) <= len(full)

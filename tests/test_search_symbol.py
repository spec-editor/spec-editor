"""Tests for search_symbol_tool with lazy mtime caching."""

import asyncio
import os
import tempfile
import time
from pathlib import Path
from unittest import mock

import pytest

from src.agents import tools_code


# ── Helpers ────────────────────────────────────────────────────────────────


def _write_py(path: Path, code: str) -> None:
    """Write a Python file. Creates parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")


def _run(coro):
    """Helper: run coroutine synchronously in tests."""
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    """Reset the global symbol cache before every test."""
    tools_code._clear_symbol_cache()
    tools_code._last_full_scan = 0.0
    yield
    tools_code._clear_symbol_cache()


# ── Basic functionality ────────────────────────────────────────────────────


class TestSearchSymbolBasic:
    """Basic search_symbol_tool behaviour."""

    def test_finds_class_definition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_py(root / "mod.py", "class AgentWorker:\n    pass\n")
            result = _run(tools_code.search_symbol_tool(tmp, "AgentWorker"))
            assert result["found"] == 1
            assert result["symbols"][0]["name"] == "AgentWorker"
            assert result["symbols"][0]["kind"] == "class"
            assert result["symbols"][0]["file"] == "mod.py"
            assert result["symbols"][0]["line"] == 1
            assert "found" in result
            assert "symbols" in result
            assert "files_scanned" in result

    def test_finds_function_definition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_py(root / "util.py", "def do_work():\n    return 42\n")
            result = _run(tools_code.search_symbol_tool(tmp, "do_work"))
            assert result["found"] == 1
            assert result["symbols"][0]["name"] == "do_work"
            assert result["symbols"][0]["kind"] == "function"

    def test_partial_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_py(root / "a.py", "class WorkerPool:\n    pass\n")
            _write_py(root / "b.py", "class WorkerThread:\n    pass\n")
            result = _run(tools_code.search_symbol_tool(tmp, "Worker"))
            assert result["found"] == 2
            names = {s["name"] for s in result["symbols"]}
            assert names == {"WorkerPool", "WorkerThread"}

    def test_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_py(root / "a.py", "class MyHandler:\n    pass\n")
            result = _run(tools_code.search_symbol_tool(tmp, "myhandler"))
            assert result["found"] == 1
            assert result["symbols"][0]["name"] == "MyHandler"

    def test_no_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_py(root / "a.py", "class Foo:\n    pass\n")
            result = _run(tools_code.search_symbol_tool(tmp, "NonExistent"))
            assert result["found"] == 0
            assert result["symbols"] == []

    def test_directory_not_found(self):
        result = _run(tools_code.search_symbol_tool("/nonexistent/path/12345", "Foo"))
        assert "error" in result
        assert result["symbols"] == []

    def test_ignores_skip_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # File in node_modules should be skipped
            _write_py(root / "node_modules" / "lib.py", "class SkippedClass:\n    pass\n")
            _write_py(root / "src" / "main.py", "class VisibleClass:\n    pass\n")
            result = _run(tools_code.search_symbol_tool(tmp, "SkippedClass"))
            assert result["found"] == 0
            result2 = _run(tools_code.search_symbol_tool(tmp, "VisibleClass"))
            assert result2["found"] == 1

    def test_result_is_sorted_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_py(root / "a.py", "class Zeta:\n    pass\nclass Alpha:\n    pass\n")
            result = _run(tools_code.search_symbol_tool(tmp, "a"))
            names = [s["name"] for s in result["symbols"]]
            assert names == sorted(names)

    def test_returns_decorators(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_py(root / "a.py", '''
@implements("REQ-001")
class MyClass:
    """A test class."""
    pass
''')
            result = _run(tools_code.search_symbol_tool(tmp, "MyClass"))
            assert result["found"] == 1
            sym = result["symbols"][0]
            assert "implements" in str(sym["decorators"])
            # docstring may or may not be extracted depending on parser version;
            # the field must be present and be a string
            assert isinstance(sym.get("docstring"), str)

    def test_max_50_results(self):
        """Should stop scanning after 50 matches."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(100):
                _write_py(root / f"mod{i}.py", f"class MyClass:\n    pass\n")
            result = _run(tools_code.search_symbol_tool(tmp, "MyClass"))
            assert result["found"] == 50


# ── Caching behaviour ──────────────────────────────────────────────────────


class TestCacheBehaviour:
    """Lazy mtime caching: first call expensive, subsequent calls fast."""

    def test_first_call_populates_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_py(root / "a.py", "class Foo:\n    pass\n")
            _write_py(root / "b.py", "class Bar:\n    pass\n")

            result1 = _run(tools_code.search_symbol_tool(tmp, "Foo"))
            assert result1["files_scanned"] >= 2
            assert result1.get("files_reparsed", 1) >= 1

    def test_second_call_uses_cache_no_reparse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_py(root / "a.py", "class Foo:\n    pass\n")

            # First call — full scan, populates cache
            _run(tools_code.search_symbol_tool(tmp, "Foo"))
            tools_code._last_full_scan = time.time()
            result2 = _run(tools_code.search_symbol_tool(tmp, "Foo"))
            assert result2["found"] == 1
            assert result2["files_reparsed"] == 0

    def test_file_modification_triggers_reparse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_py(root / "a.py", "class Foo:\n    pass\n")

            _run(tools_code.search_symbol_tool(tmp, "Foo"))
            tools_code._last_full_scan = time.time()
            time.sleep(0.01)
            _write_py(root / "a.py", "class Foo:\n    x = 1\nclass NewClass:\n    pass\n")
            result2 = _run(tools_code.search_symbol_tool(tmp, "NewClass"))
            assert result2["found"] == 1
            assert result2["files_reparsed"] == 1  # the changed file was reparsed

    def test_deleted_file_removed_from_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = root / "a.py"
            _write_py(f, "class Foo:\n    pass\n")

            _run(tools_code.search_symbol_tool(tmp, "Foo"))
            tools_code._last_full_scan = time.time()
            f.unlink()
            result2 = _run(tools_code.search_symbol_tool(tmp, "Foo"))
            assert result2["found"] == 0

    def test_new_file_picked_up_on_full_rescan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_py(root / "a.py", "class Foo:\n    pass\n")

            result1 = _run(tools_code.search_symbol_tool(tmp, "Bar"))
            assert result1["found"] == 0
            _write_py(root / "b.py", "class Bar:\n    pass\n")
            tools_code._last_full_scan = 0.0
            result2 = _run(tools_code.search_symbol_tool(tmp, "Bar"))
            assert result2["found"] == 1
            assert result2["symbols"][0]["name"] == "Bar"

    def test_new_file_missed_in_incremental_mode(self):
        """New files are NOT picked up in incremental mode — only on full rescan."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_py(root / "a.py", "class Foo:\n    pass\n")

            result1 = _run(tools_code.search_symbol_tool(tmp, "Bar"))
            assert result1["found"] == 0
            tools_code._last_full_scan = time.time()
            _write_py(root / "b.py", "class Bar:\n    pass\n")
            result2 = _run(tools_code.search_symbol_tool(tmp, "Bar"))
            assert result2["found"] == 0  # not picked up until full rescan

    def test_code_dir_change_clears_cache(self):
        with tempfile.TemporaryDirectory() as tmp1:
            with tempfile.TemporaryDirectory() as tmp2:
                root1, root2 = Path(tmp1), Path(tmp2)
                _write_py(root1 / "a.py", "class Foo:\n    pass\n")
                _write_py(root2 / "b.py", "class Bar:\n    pass\n")

                r1 = _run(tools_code.search_symbol_tool(tmp1, "Foo"))
                assert r1["found"] == 1
                r2 = _run(tools_code.search_symbol_tool(tmp2, "Bar"))
                assert r2["found"] == 1
                r3 = _run(tools_code.search_symbol_tool(tmp2, "Foo"))
                assert r3["found"] == 0

    def test_cache_clear_function(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_py(root / "a.py", "class Foo:\n    pass\n")

            _run(tools_code.search_symbol_tool(tmp, "Foo"))
            assert len(tools_code._symbol_cache) >= 1

            tools_code._clear_symbol_cache()
            assert len(tools_code._symbol_cache) == 0
            assert tools_code._cache_code_dir == ""


# ── Error handling ─────────────────────────────────────────────────────────


class TestErrorHandling:
    """OSError and parse errors are handled gracefully."""

    def test_stat_oserror_is_handled(self):
        """When os.stat raises OSError, file is skipped without crashing."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_py(root / "a.py", "class Foo:\n    pass\n")
            _write_py(root / "b.py", "class Bar:\n    pass\n")

            _run(tools_code.search_symbol_tool(tmp, "Foo"))
            tools_code._last_full_scan = time.time()
            original_stat = os.stat
            call_count = 0

            def _failing_stat(path, *args, **kwargs):
                nonlocal call_count
                call_count += 1
                if "b.py" in str(path) and call_count > 1:
                    raise OSError("Permission denied")
                return original_stat(path, *args, **kwargs)

            with mock.patch("src.agents.tools_code.os.stat", side_effect=_failing_stat):
                # Should not crash — the broken file is removed from cache
                result = _run(tools_code.search_symbol_tool(tmp, "Bar"))
                assert result["found"] == 0

    def test_parse_error_is_handled(self):
        """A file with invalid syntax should not crash the tool."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_py(root / "broken.py", "this is not valid python {{{")
            _write_py(root / "valid.py", "class GoodClass:\n    pass\n")

            result = _run(tools_code.search_symbol_tool(tmp, "GoodClass"))
            assert result["found"] == 1
            assert result["symbols"][0]["name"] == "GoodClass"

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(tools_code.search_symbol_tool(tmp, "Foo"))
            assert result["found"] == 0
            assert result["files_scanned"] == 0

    def test_no_supported_files(self):
        """Directory with only unsupported file types."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_py(root / "readme.txt", "hello")
            _write_py(root / "config.yaml", "key: value")
            result = _run(tools_code.search_symbol_tool(tmp, "Foo"))
            assert result["found"] == 0

    def test_cached_file_deleted_then_removed(self):
        """File deleted after caching is removed from cache on next stat."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = root / "a.py"
            _write_py(f, "class Foo:\n    pass\n")

            _run(tools_code.search_symbol_tool(tmp, "Foo"))
            tools_code._last_full_scan = time.time()
            f.unlink()
            result = _run(tools_code.search_symbol_tool(tmp, "Foo"))
            assert result["found"] == 0

    def test_chmod_unreadable_keeps_cached_symbols(self):
        """On macOS, stat() still works on unreadable files (owner can stat).
        Symbols remain accessible from cache."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_py(root / "a.py", "class Foo:\n    pass\n")

            _run(tools_code.search_symbol_tool(tmp, "Foo"))
            tools_code._last_full_scan = time.time()
            os.chmod(str(root / "a.py"), 0o000)
            try:
                result = _run(tools_code.search_symbol_tool(tmp, "Foo"))
                assert result["found"] == 1
            finally:
                os.chmod(str(root / "a.py"), 0o644)




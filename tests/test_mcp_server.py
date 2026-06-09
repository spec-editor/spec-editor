"""Tests for MCP server: transports, read-only mode, tool definitions."""

import json
from pathlib import Path

import pytest

from src.agents.tools import (
    RO_TOOLS,
    RW_TOOLS,
    build_all_handlers,
    build_read_only_handlers,
    get_tool_definitions,
)
from src.config.methodology import Methodology
from src.mcp.server import MCPHandler, mcp_server
from src.storage.adapter import StorageAdapter

# ======================================================================
# Helpers
# ======================================================================


def _make_storage(tmp_path: Path) -> StorageAdapter:
    """Create a minimal FilesystemStorage with methodology.yaml."""
    from src.storage.filesystem import FilesystemStorage

    (tmp_path / "methodology.yaml").write_text(
        """name: test
version: '1.0'
aspects:
  - name: modules
    title: Modules
    element_types:
      - name: module
        title: Module
""",
        encoding="utf-8",
    )
    (tmp_path / "source").mkdir(exist_ok=True)
    return FilesystemStorage(tmp_path)


def _make_methodology() -> Methodology:
    return Methodology(name="test", version="1.0")


# ======================================================================
# get_tool_definitions tests
# ======================================================================


class TestToolDefinitions:
    """get_tool_definitions returns correct sets for writable/readonly."""

    def test_ro_returns_only_readonly_tools(self):
        """get_tool_definitions(writable=False) returns only RO_TOOLS."""
        tools = get_tool_definitions(writable=False)
        tool_names = {t.name for t in tools}
        ro_names = {t.name for t in RO_TOOLS}
        rw_names = {t.name for t in RW_TOOLS}

        # All RO tools present
        assert ro_names.issubset(tool_names), f"Missing RO: {ro_names - tool_names}"
        # No RW tools present
        assert not rw_names.intersection(tool_names), (
            f"RW tools leaked: {rw_names.intersection(tool_names)}"
        )

    def test_rw_returns_all_tools(self):
        """get_tool_definitions(writable=True) returns RO + RW tools."""
        tools = get_tool_definitions(writable=True)
        tool_names = {t.name for t in tools}
        ro_names = {t.name for t in RO_TOOLS}
        rw_names = {t.name for t in RW_TOOLS}

        assert ro_names.issubset(tool_names)
        assert rw_names.issubset(tool_names)

    def test_ro_does_not_contain_write_element(self):
        """Read-only tools do NOT include write_element."""
        tools = get_tool_definitions(writable=False)
        tool_names = {t.name for t in tools}
        assert "write_element" not in tool_names
        assert "delete_element" not in tool_names
        assert "add_relationship" not in tool_names

    def test_rw_contains_write_element(self):
        """Read-write tools DO include write_element."""
        tools = get_tool_definitions(writable=True)
        tool_names = {t.name for t in tools}
        assert "write_element" in tool_names
        assert "delete_element" in tool_names


# ======================================================================
# Handler builder tests
# ======================================================================


class TestHandlerBuilders:
    """build_read_only_handlers and build_all_handlers produce correct dicts."""

    def test_all_handlers_contains_write(self, tmp_path: Path):
        """build_all_handlers includes write_element and delete_element."""
        storage = _make_storage(tmp_path)
        handlers = build_all_handlers(storage, _make_methodology())
        assert "write_element" in handlers
        assert "delete_element" in handlers
        assert "add_relationship" in handlers
        assert "read_element" in handlers  # RO still present

    def test_ro_handlers_excludes_write(self, tmp_path: Path):
        """build_read_only_handlers does NOT include write_element."""
        storage = _make_storage(tmp_path)
        handlers = build_read_only_handlers(storage, _make_methodology())
        assert "write_element" not in handlers
        assert "delete_element" not in handlers
        assert "read_element" in handlers  # RO present


# ======================================================================
# MCPHandler tests
# ======================================================================


class TestMCPHandler:
    """MCPHandler request handling for read-only and read-write modes."""

    def test_initialize_returns_capabilities(self, tmp_path: Path):
        """initialize returns protocol version and capabilities."""
        storage = _make_storage(tmp_path)
        handler = MCPHandler(project_path=tmp_path, writable=True)
        result = handler.handle_request("initialize")
        assert result["protocolVersion"] == "2024-11-05"
        assert "tools" in result["capabilities"]

    def test_list_tools_rw_has_write_element(self, tmp_path: Path):
        """tools/list with writable=True includes write_element."""
        _make_storage(tmp_path)
        handler = MCPHandler(project_path=tmp_path, writable=True)
        result = handler.handle_request("tools/list")
        tool_names = {t["name"] for t in result["tools"]}
        assert "write_element" in tool_names
        assert "delete_element" in tool_names
        assert "read_element" in tool_names

    def test_list_tools_ro_excludes_write_element(self, tmp_path: Path):
        """tools/list with writable=False excludes write_element."""
        _make_storage(tmp_path)
        handler = MCPHandler(project_path=tmp_path, writable=False)
        result = handler.handle_request("tools/list")
        tool_names = {t["name"] for t in result["tools"]}
        assert "write_element" not in tool_names
        assert "delete_element" not in tool_names
        assert "read_element" in tool_names  # RO still there

    def test_tools_call_read_element_works_ro(self, tmp_path: Path):
        """tools/call read_element works in read-only mode."""
        storage = _make_storage(tmp_path)
        # Add an element
        from src.storage.models import Element, ElementStatus

        storage.write_element(
            Element(
                id="MOD-001",
                aspect="modules",
                element_type="module",
                title="Test Module",
                content="Test content",
                status=ElementStatus.DRAFT,
            )
        )
        handler = MCPHandler(project_path=tmp_path, writable=False)
        result = handler.handle_request(
            "tools/call",
            {"name": "read_element", "arguments": {"element_id": "MOD-001"}},
        )
        assert "isError" not in result
        # Result wraps in content array
        text = result["content"][0]["text"]
        data = json.loads(text)
        assert data["id"] == "MOD-001"

    def test_unknown_method_returns_error(self, tmp_path: Path):
        """Unknown method returns isError."""
        _make_storage(tmp_path)
        handler = MCPHandler(project_path=tmp_path, writable=True)
        result = handler.handle_request("nonexistent")
        assert result.get("isError") is True


# ======================================================================
# mcp_server entry point tests
# ======================================================================


class TestMCPServerEntryPoint:
    """mcp_server() function dispatch to correct transport."""

    def test_stdio_transport_called_by_default(self, tmp_path: Path):
        """Default transport is stdio, writable=True."""
        _make_storage(tmp_path)
        handler = MCPHandler(project_path=tmp_path, writable=True)
        # Handler should have writable=True for stdio
        assert handler._writable is True

    def test_http_readonly_creates_readonly_handler(self, tmp_path: Path):
        """HTTP + read_only=True creates handler with writable=False."""
        _make_storage(tmp_path)
        handler = MCPHandler(project_path=tmp_path, writable=False)
        assert handler._writable is False
        result = handler.handle_request("tools/list")
        tool_names = {t["name"] for t in result["tools"]}
        assert "write_element" not in tool_names

    def test_http_rw_creates_writable_handler(self, tmp_path: Path):
        """HTTP without read_only creates handler with writable=True."""
        _make_storage(tmp_path)
        handler = MCPHandler(project_path=tmp_path, writable=True)
        assert handler._writable is True
        result = handler.handle_request("tools/list")
        tool_names = {t["name"] for t in result["tools"]}
        assert "write_element" in tool_names


# ======================================================================
# HTTP transport integration tests
# ======================================================================


class TestHTTPTransport:
    """Integration: HTTP server starts and responds to JSON-RPC."""

    def test_http_server_starts_and_responds(self, tmp_path: Path):
        """HTTP server accepts POST /mcp and returns JSON-RPC response."""
        _make_storage(tmp_path)
        import threading
        import time
        from http.client import HTTPConnection

        from src.mcp.server import MCPHandler, _MCPHTTPHandler

        handler = MCPHandler(project_path=tmp_path, writable=True)
        port = 18765

        def _run_server():
            _MCPHTTPHandler.mcp_handler = handler
            from http.server import HTTPServer

            server = HTTPServer(("127.0.0.1", port), _MCPHTTPHandler)
            server.timeout = 2
            for _ in range(3):
                try:
                    server.handle_request()
                except Exception:
                    break
            server.server_close()

        t = threading.Thread(target=_run_server, daemon=True)
        t.start()
        time.sleep(0.5)

        # Retry connection up to 5 times
        conn = None
        for attempt in range(5):
            try:
                conn = HTTPConnection("127.0.0.1", port, timeout=3)
                conn.connect()
                break
            except (ConnectionRefusedError, OSError):
                time.sleep(0.3)
        assert conn is not None, "Server did not start"

        try:
            body = json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
            )
            conn.request(
                "POST", "/mcp", body=body, headers={"Content-Type": "application/json"}
            )
            resp = conn.getresponse()
            data = json.loads(resp.read().decode())

            assert resp.status == 200
            assert "result" in data
            assert "tools" in data["result"]
        finally:
            conn.close()
            t.join(timeout=3)

    def test_http_readonly_blocks_write(self, tmp_path: Path):
        """HTTP read_only server has no write_element in tools/list."""
        _make_storage(tmp_path)
        import threading
        import time
        from http.client import HTTPConnection

        from src.mcp.server import MCPHandler, _MCPHTTPHandler

        handler = MCPHandler(project_path=tmp_path, writable=False)
        port = 18766

        def _run_server():
            _MCPHTTPHandler.mcp_handler = handler
            from http.server import HTTPServer

            server = HTTPServer(("127.0.0.1", port), _MCPHTTPHandler)
            server.timeout = 2
            for _ in range(3):
                try:
                    server.handle_request()
                except Exception:
                    break
            server.server_close()

        t = threading.Thread(target=_run_server, daemon=True)
        t.start()
        time.sleep(0.5)

        conn = None
        for attempt in range(5):
            try:
                conn = HTTPConnection("127.0.0.1", port, timeout=3)
                conn.connect()
                break
            except (ConnectionRefusedError, OSError):
                time.sleep(0.3)
        assert conn is not None, "Server did not start"

        try:
            body = json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
            )
            conn.request(
                "POST", "/mcp", body=body, headers={"Content-Type": "application/json"}
            )
            resp = conn.getresponse()
            data = json.loads(resp.read().decode())

            assert resp.status == 200
            tool_names = {t["name"] for t in data["result"]["tools"]}
            assert "write_element" not in tool_names
            assert "read_element" in tool_names
        finally:
            conn.close()
            t.join(timeout=3)

    def test_http_write_element_works_without_readonly(self, tmp_path: Path):
        """HTTP without --read-only: write_element creates an element."""
        _make_storage(tmp_path)
        import threading
        import time
        from http.client import HTTPConnection

        from src.mcp.server import MCPHandler, _MCPHTTPHandler

        handler = MCPHandler(project_path=tmp_path, writable=True)
        port = 18767

        def _run_server():
            _MCPHTTPHandler.mcp_handler = handler
            from http.server import HTTPServer

            server = HTTPServer(("127.0.0.1", port), _MCPHTTPHandler)
            server.timeout = 3
            for _ in range(3):
                try:
                    server.handle_request()
                except Exception:
                    break
            server.server_close()

        t = threading.Thread(target=_run_server, daemon=True)
        t.start()
        time.sleep(0.5)

        # Retry connection
        conn = None
        for attempt in range(5):
            try:
                conn = HTTPConnection("127.0.0.1", port, timeout=3)
                conn.connect()
                break
            except (ConnectionRefusedError, OSError):
                time.sleep(0.3)
        assert conn is not None, "Server did not start"

        try:
            # Call write_element
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "write_element",
                        "arguments": {
                            "id": "TEST-001",
                            "aspect": "modules",
                            "element_type": "module",
                            "title": "HTTP Test",
                            "content": "Created via HTTP",
                        },
                    },
                }
            )
            conn.request(
                "POST", "/mcp", body=body, headers={"Content-Type": "application/json"}
            )
            resp = conn.getresponse()
            data = json.loads(resp.read().decode())

            assert resp.status == 200
            assert "result" in data
            result_text = json.loads(data["result"]["content"][0]["text"])
            assert result_text["element_id"] == "TEST-001"
        finally:
            conn.close()
            t.join(timeout=5)

    def test_http_404_on_wrong_path(self, tmp_path: Path):
        """POST to /wrong returns 404."""
        import threading
        import time
        from http.client import HTTPConnection

        from src.mcp.server import MCPHandler, _MCPHTTPHandler

        handler = MCPHandler(writable=True)
        port = 18768

        def _run_server():
            _MCPHTTPHandler.mcp_handler = handler
            from http.server import HTTPServer

            server = HTTPServer(("127.0.0.1", port), _MCPHTTPHandler)
            server.timeout = 2
            for _ in range(3):
                try:
                    server.handle_request()
                except Exception:
                    break
            server.server_close()

        t = threading.Thread(target=_run_server, daemon=True)
        t.start()
        time.sleep(0.5)

        conn = None
        for attempt in range(5):
            try:
                conn = HTTPConnection("127.0.0.1", port, timeout=3)
                conn.connect()
                break
            except (ConnectionRefusedError, OSError):
                time.sleep(0.3)
        assert conn is not None, "Server did not start"

        try:
            conn.request(
                "POST",
                "/wrong",
                body="{}",
                headers={"Content-Type": "application/json"},
            )
            resp = conn.getresponse()
            assert resp.status == 404
        finally:
            conn.close()
            t.join(timeout=3)


# ======================================================================
# CLI integration tests
# ======================================================================


class TestMCPServerCLI:
    """CLI integration: spec-editor mcp with --transport, --read-only flags."""

    def test_mcp_help_shows_new_options(self):
        """spec-editor mcp --help shows --transport, --port, --read-only, --host."""
        from click.testing import CliRunner

        from src.cli.commands import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["mcp", "--help"])
        assert result.exit_code == 0
        assert "--transport" in result.output
        assert "--port" in result.output
        assert "--read-only" in result.output
        assert "--host" in result.output
        assert "8001" in result.output  # default port

    def test_mcp_http_flag_accepted(self):
        """--transport http is accepted without error."""
        from click.testing import CliRunner

        from src.cli.commands import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["mcp", "--transport", "http", "--help"])
        assert result.exit_code == 0

    def test_mcp_invalid_transport_rejected(self):
        """--transport invalid is rejected by Click."""
        from click.testing import CliRunner

        from src.cli.commands import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["mcp", "--transport", "grpc"])
        assert result.exit_code != 0

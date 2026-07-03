"""E2E tests for MCP server: HTTP endpoints, SSE events, full tool round-trips.

Tests run a real MCP server in-process (HTTP server in a thread),
providing true end-to-end coverage without subprocess overhead.

References:
    E2E-MCP-001: MCP server start/stop
    E2E-SSE-001: SSE event streaming
"""

from __future__ import annotations

import json
import pytest
import threading
import time
from http.client import HTTPConnection
from pathlib import Path

from src.mcp.server import MCPHandler, _MCPHTTPHandler

# =============================================================================
# Helpers
# =============================================================================


def _make_project(tmp_path: Path, suffix: str = "") -> Path:
    """Create a minimal spec-editor project."""
    name = f"test-project{suffix}"
    project = tmp_path / name
    project.mkdir(exist_ok=True)
    (project / "methodology.yaml").write_text(
        "name: test\nversion: '1.0'\naspects:\n  - name: modules\n    title: Modules\n    element_types:\n      - name: module\n        title: Module\n",
        encoding="utf-8",
    )
    (project / "source").mkdir()
    (project / "aspects").mkdir()
    (project / "aspects" / "modules").mkdir()
    return project


class _McpServerFixture:
    """Fixture: MCP server running in a thread."""

    def __init__(
        self,
        tmp_path: Path,
        port: int = 15238,
        read_only: bool = False,
        suffix: str = "",
    ):
        self.port = port
        project = _make_project(tmp_path, suffix)
        self.handler = MCPHandler(project_path=project, writable=not read_only)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        _MCPHTTPHandler.mcp_handler = self.handler
        from http.server import ThreadingHTTPServer

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _MCPHTTPHandler)
        self._server.timeout = 1

        def _serve():
            while not self._stop_event.is_set():
                try:
                    self._server.handle_request()
                except Exception:
                    break

        self._thread = threading.Thread(target=_serve, daemon=True)
        self._thread.start()
        time.sleep(0.1)

    def stop(self):
        self._stop_event.set()
        try:
            self._server.server_close()
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=2)

    def request(self, method: str, params: dict | None = None) -> dict:
        """Make a JSON-RPC request."""
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        }
        conn = HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(
            "POST",
            "/mcp",
            body=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()
        return data

    def call_tool(self, name: str, args: dict | None = None) -> dict:
        """Call an MCP tool."""
        return self.request(
            "tools/call",
            {"name": name, "arguments": args or {}},
        )

    def parse_tool_result(self, result: dict) -> dict:
        """Extract parsed JSON from a tool result."""
        text = result["result"]["content"][0]["text"]
        return json.loads(text)


# =============================================================================
# E2E: Server lifecycle
# =============================================================================


class TestMcpServerE2E:
    """E2E: MCP server starts, responds, and shuts down."""

    def test_server_initialize(self, tmp_path: Path):
        srv = _McpServerFixture(tmp_path, port=16238)
        srv.start()
        try:
            result = srv.request("initialize")
            assert "result" in result
            info = result["result"]["serverInfo"]
            assert info["name"] == "spec-editor-mcp"
            assert "version" in info
            assert info["editor"] == "standalone"
        finally:
            srv.stop()

    def test_server_lists_all_tools(self, tmp_path: Path):
        srv = _McpServerFixture(tmp_path, port=16239)
        srv.start()
        try:
            result = srv.request("tools/list")
            tools = result["result"]["tools"]
            tool_names = {t["name"] for t in tools}
            assert "read_element" in tool_names
            assert "write_element" in tool_names
            assert "list_projects" in tool_names
            assert "get_file_tree" in tool_names
        finally:
            srv.stop()

    def test_read_only_blocks_write(self, tmp_path: Path):
        srv = _McpServerFixture(tmp_path, port=16240, read_only=True)
        srv.start()
        try:
            result = srv.call_tool(
                "write_element",
                {
                    "aspect": "modules",
                    "element_type": "module",
                    "id": "X",
                    "title": "Test",
                },
            )
            assert result["result"].get("isError") is True
        finally:
            srv.stop()


# =============================================================================
# E2E: Tool execution round-trips
# =============================================================================


class TestMcpToolsE2E:
    """E2E: Write, read, switch, list, diagram — full round-trips."""

    def test_write_and_read_element(self, tmp_path: Path):
        srv = _McpServerFixture(tmp_path, port=16241)
        srv.start()
        try:
            # Write
            result = srv.call_tool(
                "write_element",
                {
                    "aspect": "modules",
                    "element_type": "module",
                    "id": "MOD-E2E-001",
                    "title": "E2E Auth Module",
                    "content": "Handles authentication",
                },
            )
            assert not result["result"].get("isError")

            # Read back
            result = srv.call_tool("read_element", {"element_id": "MOD-E2E-001"})
            data = srv.parse_tool_result(result)
            assert data["id"] == "MOD-E2E-001"
            assert data["title"] == "E2E Auth Module"
            assert data["aspect"] == "modules"
        finally:
            srv.stop()

    def test_write_and_delete_element(self, tmp_path: Path):
        srv = _McpServerFixture(tmp_path, port=16242)
        srv.start()
        try:
            # Write
            srv.call_tool(
                "write_element",
                {
                    "aspect": "modules",
                    "element_type": "module",
                    "id": "MOD-DEL-001",
                    "title": "To Delete",
                    "content": "",
                },
            )

            # Delete
            result = srv.call_tool("delete_element", {"element_id": "MOD-DEL-001"})
            assert not result["result"].get("isError")

            # Read should fail
            result = srv.call_tool("read_element", {"element_id": "MOD-DEL-001"})
            data = srv.parse_tool_result(result)
            assert "error" in data
        finally:
            srv.stop()

    
class TestSseE2E:
    """E2E: SSE hub integration with MCP server."""

    def test_sse_hub_receives_events_from_mcp(self, tmp_path: Path):
        """SSE hub subscribed to MCP receives events from tool calls."""
        from src.mcp.sse import SseConnection

        srv = _McpServerFixture(tmp_path, port=16251, suffix="-ssehub")
        srv.start()
        try:
            # Subscribe directly to hub (bypass HTTP for test reliability)
            conn = SseConnection()
            srv.handler.sse_hub.add_connection(conn)

            # Trigger write_element
            srv.call_tool(
                "write_element",
                {
                    "aspect": "modules",
                    "element_type": "module",
                    "id": "MOD-SSE-E2E",
                    "title": "SSE E2E Test",
                    "content": "",
                },
            )

            # Should receive element_updated
            result = conn.read(timeout=3.0)
            assert b"element_updated" in result
            assert b"MOD-SSE-E2E" in result
        finally:
            srv.stop()

    
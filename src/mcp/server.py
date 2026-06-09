"""MCP Server — stdio/json-rpc and HTTP transports for external agents.

Launch: spec-editor mcp [-p <project>] [--transport stdio|http] [--port PORT] [--read-only]

Supports:
  - stdio transport (default): JSON-RPC via stdin/stdout, full read+write
  - http transport: HTTP POST /mcp JSON-RPC, optional --read-only

Connects third-party MCP clients (Cursor, Aider, Claude Desktop, Zed) to storage.

IMPORTANT: structlog must be configured BEFORE any project imports,
because module-level get_logger() calls trigger auto-configuration.
"""

# ── Configure logging FIRST, before any project imports ──
import logging
import sys

for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)
logging.basicConfig(stream=sys.stderr, level=logging.ERROR, format="%(message)s")

import structlog

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(colors=False),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.stdlib.LoggerFactory(),
)

# ── Now safe to import project modules ──
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from src.agents.tools import (
    build_all_handlers,
    build_read_only_handlers,
    get_tool_definitions,
)
from src.config.methodology import load_methodology
from src.storage.filesystem import FilesystemStorage

# ======================================================================
# Core MCP logic (transport-agnostic)
# ======================================================================


class MCPHandler:
    """Handles JSON-RPC MCP requests independently of transport."""

    def __init__(
        self,
        project_path: Path | None = None,
        writable: bool = True,
    ) -> None:
        self._writable = writable
        self._state: dict = {
            "storage": None,
            "handlers": {},
            "source_dir": "",
            "project_path": "",
        }
        if project_path:
            self._init_state(project_path)

    # ── Request dispatch ──

    def handle_request(self, method: str, params: dict | None = None) -> dict:
        """Handle a single JSON-RPC method call. Returns result dict."""
        params = params or {}

        if method == "initialize":
            return {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "spec-editor-mcp", "version": "1.0"},
            }

        if method == "tools/list":
            return {"tools": self._build_schemas()}

        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            if tool_name == "switch_project":
                new_path = arguments.get("path", "")
                if new_path:
                    self._init_state(Path(new_path).resolve())
                    return self._content(
                        {
                            "project": self._state["project_path"],
                            "elements": len(self._state["storage"].list_all())
                            if self._state["storage"]
                            else 0,
                        }
                    )
                return self._error("path required")

            handler = self._state["handlers"].get(tool_name)
            if handler:
                try:
                    import asyncio

                    result = handler(**arguments)
                    if asyncio.iscoroutine(result):
                        result = asyncio.run(result)
                    return self._content(result)
                except Exception as exc:
                    return self._error(str(exc))
            return self._error("No project loaded. Use switch_project first.")

        return self._error(f"Unknown method: {method}")

    # ── Schema building ──

    def _build_schemas(self) -> list:
        tools = get_tool_definitions(writable=self._writable)
        schemas = [
            {"name": t.name, "description": t.description, "inputSchema": t.parameters}
            for t in tools
        ]
        schemas.append(
            {
                "name": "switch_project",
                "description": "[Project] Switch to a different project by path. Requires existing methodology.yaml in the target directory.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path to project directory",
                        }
                    },
                    "required": ["path"],
                },
            }
        )
        return schemas

    # ── State initialisation ──

    def _init_state(self, project_path: Path) -> None:
        storage = FilesystemStorage(project_path)
        method_path = project_path / "methodology.yaml"
        methodology = (
            load_methodology(method_path)
            if method_path.exists()
            else _fake_methodology()
        )
        builder = build_all_handlers if self._writable else build_read_only_handlers
        self._state["storage"] = storage
        self._state["handlers"] = builder(
            storage, methodology, source_dir=str(project_path / "source")
        )
        self._state["source_dir"] = str(project_path / "source")
        self._state["project_path"] = str(project_path)

    # ── Response helpers ──

    @staticmethod
    def _content(data) -> dict:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(data, ensure_ascii=False, default=str),
                }
            ]
        }

    @staticmethod
    def _error(msg: str) -> dict:
        return {"content": [{"type": "text", "text": msg}], "isError": True}


# ======================================================================
# Stdio transport
# ======================================================================


def run_stdio_server(handler: MCPHandler) -> None:
    """Run MCP server over stdio (JSON-RPC via stdin/stdout)."""
    for line in sys.stdin:
        try:
            request = json.loads(line.strip())
        except json.JSONDecodeError:
            continue

        method = request.get("method", "")
        req_id = request.get("id", 0)
        params = request.get("params", {})

        if method == "shutdown":
            break

        result = handler.handle_request(method, params)
        _respond_stdio(req_id, result)


def _respond_stdio(req_id, result: dict) -> None:
    sys.stdout.write(
        json.dumps(
            {"jsonrpc": "2.0", "id": req_id, "result": result}, ensure_ascii=False
        )
        + "\n"
    )
    sys.stdout.flush()


# ======================================================================
# HTTP transport
# ======================================================================


class _MCPHTTPHandler(BaseHTTPRequestHandler):
    """HTTP request handler for MCP JSON-RPC over POST /mcp."""

    mcp_handler: MCPHandler = None  # type: ignore[assignment]

    def log_message(self, format, *args):
        pass  # Suppress HTTP server logs to stderr

    def do_POST(self):
        if self.path != "/mcp":
            self.send_error(404)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            request = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        method = request.get("method", "")
        req_id = request.get("id", 0)
        params = request.get("params", {})

        if method == "shutdown":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"jsonrpc":"2.0","id":0,"result":"ok"}')
            return

        result = self.mcp_handler.handle_request(method, params)
        response = {"jsonrpc": "2.0", "id": req_id, "result": result}

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):
        """CORS preflight."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def run_http_server(handler: MCPHandler, host: str, port: int) -> None:
    """Run MCP server over HTTP POST /mcp."""
    _MCPHTTPHandler.mcp_handler = handler  # type: ignore[assignment]
    server = HTTPServer((host, port), _MCPHTTPHandler)
    print(
        f"[spec-editor] MCP HTTP server listening on http://{host}:{port}/mcp",
        file=sys.stderr,
    )
    print(f"[spec-editor] Read-only: {not handler._writable}", file=sys.stderr)
    print(
        "[spec-editor] Waiting for agent connection... (Ctrl+C to stop)",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[spec-editor] Shutting down...", file=sys.stderr)
        server.shutdown()


# ======================================================================
# Entry point
# ======================================================================


def mcp_server(
    path: str | None = None,
    transport: str = "stdio",
    port: int = 8001,
    read_only: bool = False,
    host: str = "127.0.0.1",
) -> None:
    """Start MCP server with specified transport and mode.

    Args:
        path: Path to spec-editor project directory
        transport: "stdio" (default) or "http"
        port: HTTP port (default 8001)
        read_only: If True, only read-only tools are registered (HTTP only)
        host: HTTP host to bind to (default 127.0.0.1)
    """
    project_path = Path(path).resolve() if path else None

    # For stdio, always full access (local, no security risk)
    writable = True if transport == "stdio" else not read_only

    handler = MCPHandler(project_path=project_path, writable=writable)

    if transport == "http":
        run_http_server(handler, host, port)
    else:
        run_stdio_server(handler)


# ======================================================================
# Helpers
# ======================================================================


def _fake_methodology():
    from src.config.methodology import Methodology

    return Methodology(name="mcp", version="1.0")

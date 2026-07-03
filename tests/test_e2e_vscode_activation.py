"""E2E: VSCode extension activation — starts MCP and responds to commands.

Verifies:
- Extension can start the MCP server via spawned process
- MCP server responds to initialize and tool calls
- Extension can call tools through the MCP client

Unlike lifecycle tests, this tests the actual runtime behavior.
"""

from __future__ import annotations

import json
import pytest
import subprocess
import sys
import time
from http.client import HTTPConnection
from pathlib import Path

# =============================================================================
# Constants
# =============================================================================

MCP_PORT = 9088  # Test port — different from default 8088
EXT_DIR = Path(__file__).parent.parent / "packages" / "vscode-extension"


# =============================================================================
# Helpers
# =============================================================================


def _make_project(tmp_path: Path) -> Path:
    """Create a minimal spec-editor project."""
    project = tmp_path / "vscode-activation-test"
    project.mkdir(exist_ok=True)
    (project / "methodology.yaml").write_text(
        "name: vscode-e2e\nversion: '1.0'\naspects:\n  - name: modules\n    title: Modules\n    element_types:\n      - name: module\n        title: Module\n",
        encoding="utf-8",
    )
    (project / "source").mkdir(exist_ok=True)
    (project / "aspects").mkdir(exist_ok=True)
    (project / "aspects" / "modules").mkdir(exist_ok=True)
    return project


def _start_mcp(project_path: str, port: int) -> subprocess.Popen:
    """Start MCP server — same way VSCode extension does."""
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"from src.mcp.server import mcp_server; mcp_server(path='{project_path}', transport='http', port={port}, host='127.0.0.1')",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(Path(__file__).parent.parent),
    )
    return proc


def _wait_for_port(port: int, timeout: float = 10.0) -> bool:
    """Wait for server to be ready."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=1.0)
            conn.request(
                "POST", "/mcp", body="{}", headers={"Content-Type": "application/json"}
            )
            resp = conn.getresponse()
            resp.read()
            conn.close()
            return True
        except Exception:
            time.sleep(0.2)
    return False


def _mcp_call(port: int, tool: str, args: dict) -> dict:
    """Call an MCP tool."""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }
    )
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(
        "POST", "/mcp", body=body, headers={"Content-Type": "application/json"}
    )
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return data


# =============================================================================
# E2E: Extension activation
# =============================================================================


class TestVscodeActivation:
    """E2E: Extension activates and MCP communication works."""

    def test_mcp_server_starts_and_responds(self, tmp_path: Path):
        """Extension can start MCP server and call initialize."""
        project = _make_project(tmp_path)
        proc = _start_mcp(str(project), MCP_PORT)

        try:
            assert _wait_for_port(MCP_PORT, timeout=10.0), "MCP server didn't start"

            # Simulate what extension does in activate():
            # 1. Initialize MCP session
            conn = HTTPConnection("127.0.0.1", MCP_PORT, timeout=10)
            conn.request(
                "POST",
                "/mcp",
                body=json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
                ),
                headers={"Content-Type": "application/json"},
            )
            resp = conn.getresponse()
            init_data = json.loads(resp.read())
            conn.close()

            assert init_data["result"]["serverInfo"]["name"] == "spec-editor-mcp"
            assert init_data["result"]["serverInfo"]["editor"] == "standalone"

            # 2. List tools — verifies handler is fully initialized
            result = _mcp_call(MCP_PORT, "get_methodology", {})
            text = result["result"]["content"][0]["text"]
            method_data = json.loads(text)
            assert method_data["name"] == "vscode-e2e"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_extension_can_write_and_read(self, tmp_path: Path):
        """Extension can write and read spec elements via MCP."""
        project = _make_project(tmp_path)
        proc = _start_mcp(str(project), MCP_PORT)

        try:
            assert _wait_for_port(MCP_PORT, timeout=10.0)

            # Write element via MCP (same way extension does)
            result = _mcp_call(
                MCP_PORT,
                "write_element",
                {
                    "aspect": "modules",
                    "element_type": "module",
                    "id": "VSCODE-E2E-001",
                    "title": "VSCode E2E Element",
                    "content": "Created by VSCode extension E2E test",
                },
            )
            assert "result" in result
            assert not result["result"].get("isError")

            # Read back
            result = _mcp_call(
                MCP_PORT, "read_element", {"element_id": "VSCODE-E2E-001"}
            )
            text = result["result"]["content"][0]["text"]
            element = json.loads(text)
            assert element["id"] == "VSCODE-E2E-001"
            assert element["title"] == "VSCode E2E Element"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    
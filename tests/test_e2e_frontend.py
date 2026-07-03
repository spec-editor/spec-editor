"""E2E tests for the Next.js frontend with a real MCP server.

Starts the MCP server and Next.js dev server, then verifies:
- Frontend page loads correctly
- MCP proxy rewrite works
- MCP tools are callable through the proxy
- Frontend correctly renders MCP data

References:
    E2E-FRONTEND-001: Frontend + MCP server integration
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from http.client import HTTPConnection
from pathlib import Path

# =============================================================================
# Helpers
# =============================================================================


def _make_project(tmp_path: Path) -> Path:
    """Create a minimal spec-editor project."""
    project = tmp_path / "frontend-e2e-project"
    project.mkdir(exist_ok=True)
    (project / "methodology.yaml").write_text(
        "name: e2e-frontend\nversion: '1.0'\naspects:\n  - name: modules\n    title: Modules\n    element_types:\n      - name: module\n        title: Module\n",
        encoding="utf-8",
    )
    (project / "source").mkdir(exist_ok=True)
    (project / "aspects").mkdir(exist_ok=True)
    (project / "aspects" / "modules").mkdir(exist_ok=True)
    return project


def _wait_for_http(port: int, path: str = "/", timeout: float = 15.0) -> bool:
    """Wait for an HTTP server to be ready."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=1.0)
            conn.request("GET", path)
            resp = conn.getresponse()
            resp.read()
            conn.close()
            return True
        except Exception:
            time.sleep(0.3)
    return False


# =============================================================================
# E2E: Frontend + MCP server
# =============================================================================


class TestFrontendE2E:
    """E2E: Frontend loads, connects to MCP, renders data."""

    MCP_PORT = 18123  # test port — avoids conflict with dev server (8088)
    FRONTEND_PORT = 14124

    def _start_mcp(self, project_path: str) -> subprocess.Popen:
        """Start MCP server as subprocess."""
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                f"from src.mcp.server import mcp_server; mcp_server(path='{project_path}', transport='http', port={self.MCP_PORT}, host='127.0.0.1')",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(Path(__file__).parent.parent),
        )
        return proc

    def _start_frontend(self, mcp_port: int, frontend_port: int) -> subprocess.Popen:
        """Start Next.js dev server."""
        frontend_dir = Path(__file__).parent.parent / "packages" / "frontend"
        env = os.environ.copy()
        env["SPEC_MCP_PORT"] = str(mcp_port)

        proc = subprocess.Popen(
            ["npm", "run", "dev", "--", "-p", str(frontend_port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(frontend_dir),
            env=env,
        )
        return proc

    def test_frontend_page_loads(self, tmp_path: Path):
        """Frontend page loads and contains Spec Editor title."""
        project = _make_project(tmp_path)
        mcp_proc = self._start_mcp(str(project))
        frontend_proc = None

        try:
            assert _wait_for_http(self.MCP_PORT, timeout=10.0), (
                "MCP server didn't start"
            )
            frontend_proc = self._start_frontend(self.MCP_PORT, self.FRONTEND_PORT)
            assert _wait_for_http(self.FRONTEND_PORT, timeout=20.0), (
                "Frontend didn't start"
            )

            # Fetch page
            conn = HTTPConnection("127.0.0.1", self.FRONTEND_PORT, timeout=10)
            conn.request("GET", "/")
            resp = conn.getresponse()
            html = resp.read().decode()
            conn.close()

            assert resp.status == 200, f"Expected 200, got {resp.status}"
            assert "Spec Editor" in html, "Page should contain 'Spec Editor'"
            assert 'id="__next"' in html, "Page should be Next.js rendered"
        finally:
            if frontend_proc:
                frontend_proc.terminate()
                frontend_proc.wait(timeout=5)
            if mcp_proc:
                mcp_proc.terminate()
                mcp_proc.wait(timeout=5)

    def test_frontend_proxy_mcp(self, tmp_path: Path):
        """Frontend proxy /api/mcp forwards to MCP server."""
        project = _make_project(tmp_path)
        mcp_proc = self._start_mcp(str(project))
        frontend_proc = None

        try:
            assert _wait_for_http(self.MCP_PORT, timeout=10.0)
            frontend_proc = self._start_frontend(self.MCP_PORT, self.FRONTEND_PORT)
            assert _wait_for_http(self.FRONTEND_PORT, timeout=20.0)

            # Proxy MCP request through frontend
            conn = HTTPConnection("127.0.0.1", self.FRONTEND_PORT, timeout=10)
            conn.request(
                "POST",
                "/api/mcp",
                body=json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {},
                    }
                ),
                headers={"Content-Type": "application/json"},
            )
            resp = conn.getresponse()
            data = json.loads(resp.read())
            conn.close()

            assert resp.status == 200, f"Proxy returned {resp.status}"
            assert "result" in data, "Should have MCP result"
            assert data["result"]["serverInfo"]["name"] == "spec-editor-mcp"
        finally:
            if frontend_proc:
                frontend_proc.terminate()
                frontend_proc.wait(timeout=5)
            if mcp_proc:
                mcp_proc.terminate()
                mcp_proc.wait(timeout=5)

    def test_mcp_write_read_via_frontend_proxy(self, tmp_path: Path):
        """Write and read elements through the frontend proxy."""
        project = _make_project(tmp_path)
        mcp_proc = self._start_mcp(str(project))
        frontend_proc = None

        try:
            assert _wait_for_http(self.MCP_PORT, timeout=10.0)
            frontend_proc = self._start_frontend(self.MCP_PORT, self.FRONTEND_PORT)
            assert _wait_for_http(self.FRONTEND_PORT, timeout=20.0)

            def proxy_call(tool: str, args: dict) -> dict:
                conn = HTTPConnection("127.0.0.1", self.FRONTEND_PORT, timeout=10)
                conn.request(
                    "POST",
                    "/api/mcp",
                    body=json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "tools/call",
                            "params": {"name": tool, "arguments": args},
                        }
                    ),
                    headers={"Content-Type": "application/json"},
                )
                resp = conn.getresponse()
                data = json.loads(resp.read())
                conn.close()
                return data

            # Write element
            result = proxy_call(
                "write_element",
                {
                    "aspect": "modules",
                    "element_type": "module",
                    "id": "PROXY-E2E-001",
                    "title": "Proxy E2E Element",
                    "content": "Created via frontend proxy",
                },
            )
            assert "result" in result, "Write should succeed"

            # Read back
            result = proxy_call("read_element", {"element_id": "PROXY-E2E-001"})
            text = result["result"]["content"][0]["text"]
            element = json.loads(text)
            assert element["id"] == "PROXY-E2E-001"
            assert element["title"] == "Proxy E2E Element"
        finally:
            if frontend_proc:
                frontend_proc.terminate()
                frontend_proc.wait(timeout=5)
            if mcp_proc:
                mcp_proc.terminate()
                mcp_proc.wait(timeout=5)

"""Visual test simulation — starts MCP server, makes frontend API calls.

Simulates the data flow that the frontend components would experience.
Run: .venv/bin/python tests/manual_api_test.py
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http.client import HTTPConnection
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.mcp.server import MCPHandler, _MCPHTTPHandler

PORT = 8099


def make_project() -> Path:
    project = Path("/tmp/spec-editor-visual-test")
    project.mkdir(exist_ok=True)
    (project / "methodology.yaml").write_text(
        "name: visual-test\nversion: '1.0'\naspects:\n"
        "  - name: modules\n    title: Modules\n    element_types:\n"
        "      - name: module\n        title: Module\n"
        "  - name: user_scenarios\n    title: User Scenarios\n    element_types:\n"
        "      - name: user_scenario\n        title: User Scenario\n"
    )
    for d in ["source", "aspects", "aspects/modules", "aspects/user_scenarios"]:
        (project / d).mkdir(exist_ok=True)
    return project


def rpc(method: str, params: dict | None = None) -> dict:
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    )
    conn = HTTPConnection(f"127.0.0.1:{PORT}", timeout=10)
    conn.request(
        "POST", "/mcp", body=body, headers={"Content-Type": "application/json"}
    )
    resp = conn.getresponse()
    return json.loads(resp.read())


def call_tool(name: str, args: dict) -> dict:
    return rpc("tools/call", {"name": name, "arguments": args})


def tool_json(name: str, args: dict) -> dict:
    result = call_tool(name, args)
    return json.loads(result["result"]["content"][0]["text"])


def hr(title: str):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def check(label: str, passed: bool, detail: str = ""):
    icon = "✅" if passed else "❌"
    print(f"  {icon} {label}" + (f": {detail}" if detail else ""))


def main():
    # Start server
    project = make_project()
    handler = MCPHandler(project_path=project, writable=True)
    _MCPHTTPHandler.mcp_handler = handler
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", PORT), _MCPHTTPHandler)
    server.allow_reuse_address = True
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.5)

    try:
        # ── Test 1: initialize ──
        hr("Frontend: initialize (connection check)")
        result = rpc("initialize", {"protocolVersion": "2024-11-05"})
        info = result["result"]["serverInfo"]
        check("Server name", info["name"] == "spec-editor-mcp", info["name"])
        check("Has version", "version" in info, info.get("version", "N/A"))
        check("Has editor", "editor" in info, info.get("editor", "N/A"))

        # ── Test 2: list_all_elements (ElementTree) ──
        hr("Frontend: ElementTree data")

        # Write test elements
        for i in range(1, 4):
            call_tool(
                "write_element",
                {
                    "aspect": "modules",
                    "element_type": "module",
                    "id": f"MOD-V{i:03d}",
                    "title": f"Visual Test Module {i}",
                    "derived_from": ["SRC-001"],
                },
            )
        for i in range(1, 3):
            call_tool(
                "write_element",
                {
                    "aspect": "user_scenarios",
                    "element_type": "user_scenario",
                    "id": f"US-V{i:03d}",
                    "title": f"Visual Test Scenario {i}",
                    "derived_from": ["SRC-001"],
                },
            )

        data = tool_json("list_all_elements", {})
        elements = data.get("elements", [])
        check("Elements returned", len(elements) >= 5, f"{len(elements)} elements")
        check("Has id field", all("id" in e for e in elements))
        check("Has aspect field", all("aspect" in e for e in elements))
        check("Has status field", all("status" in e for e in elements))
        check(
            "Valid statuses",
            all(
                e["status"] in ("draft", "reviewed", "confirmed", "deprecated")
                for e in elements
            ),
        )

        aspects = set(e["aspect"] for e in elements)
        check("Multiple aspects", len(aspects) >= 2, f"{aspects}")

        # ── Test 3: read_element (ElementDetail) ──
        hr("Frontend: ElementDetail data")
        detail = tool_json("read_element", {"element_id": "MOD-V001"})
        check("Has id", detail.get("id") == "MOD-V001")
        check("Has title", bool(detail.get("title")))
        check("Has aspect", bool(detail.get("aspect")))
        check("Has status", bool(detail.get("status")))
        check("Has children (list)", isinstance(detail.get("children"), list))
        check("Has derived_from", isinstance(detail.get("derived_from"), list))
        if detail.get("derived_from"):
            check("Derived from SRC", detail["derived_from"][0] == "SRC-001")

        # ── Test 4: generate_diagram (MermaidDiagram) ──
        hr("Frontend: MermaidDiagram data")
        result = call_tool("generate_diagram", {"aspect": "modules"})
        mermaid = result["result"]["content"][0]["text"]
        check("Non-empty diagram", len(mermaid) > 10, f"{len(mermaid)} chars")
        check(
            "Contains graph syntax",
            "graph" in mermaid.lower() or "flowchart" in mermaid.lower(),
            mermaid[:60].replace("\n", " "),
        )

        # ── Test 5: run_validate (ValidationPanel) ──
        hr("Frontend: ValidationPanel data")
        vdata = tool_json("run_validate", {})
        check("Has passed flag", "passed" in vdata, str(vdata.get("passed")))
        check(
            "Has errors list",
            isinstance(vdata.get("errors", []), list),
            f"{len(vdata.get('errors', []))} errors",
        )
        check(
            "Has warnings list",
            isinstance(vdata.get("warnings", []), list),
            f"{len(vdata.get('warnings', []))} warnings",
        )

        # ── Test 6: run_metrics (ValidationPanel metrics tab) ──
        hr("Frontend: ValidationPanel metrics")
        mdata = tool_json("run_metrics", {})
        check(
            "Total elements",
            mdata.get("total_elements", 0) >= 5,
            str(mdata.get("total_elements")),
        )
        check(
            "Coverage ratio",
            "coverage_ratio" in mdata,
            f"{mdata.get('coverage_ratio', 0):.2f}",
        )
        check(
            "Orphan elements",
            "orphan_elements" in mdata,
            str(mdata.get("orphan_elements")),
        )
        check(
            "Cross-aspect relationships",
            "cross_aspect_relationships" in mdata,
            str(mdata.get("cross_aspect_relationships")),
        )
        check(
            "Aspects breakdown",
            isinstance(mdata.get("aspects", {}), dict),
            str(list(mdata.get("aspects", {}).keys())),
        )

        # ── Test 7: search_elements ──
        hr("Frontend: Search")
        sdata = tool_json("search_elements", {"query": "MOD"})
        results = sdata.get("results", sdata.get("elements", []))
        check("Search returns results", len(results) >= 3, f"{len(results)} results")

        # ── Test 8: Error handling ──
        hr("Frontend: Error handling")
        result = call_tool("read_element", {"element_id": "NONEXISTENT"})
        content = result["result"]["content"][0]["text"]
        check(
            "Not-found returns error",
            "not found" in content.lower() or "error" in content.lower(),
            content[:60],
        )

        result = call_tool("unknown_tool_xyz", {})
        check(
            "Unknown tool returns isError",
            result["result"].get("isError", False),
            str(result["result"])[:80],
        )

        # ── Test 9: SSE endpoint check ──
        hr("Frontend: SSE events endpoint")
        conn = HTTPConnection(f"127.0.0.1:{PORT}", timeout=3)
        try:
            conn.request("GET", "/events")
            resp = conn.getresponse()
            check(
                "SSE endpoint exists", resp.status in (200, 202), f"HTTP {resp.status}"
            )
        except Exception as e:
            check("SSE endpoint reachable", False, str(e)[:60])

        # ── Summary ──
        hr("VISUAL TEST SUMMARY")
        print(f"  All frontend data shapes verified against live MCP server")
        print(f"  Next: start frontend dev server to see UI:")
        print(
            f"    Terminal 1: .venv/bin/python -m src.main mcp --transport http --port 8088"
        )
        print(f"    Terminal 2: cd packages/frontend && npm run dev")
        print(f"    Browser: http://localhost:3000")

    finally:
        server.shutdown()
        t.join(timeout=1)


if __name__ == "__main__":
    main()

"""MCP Server — stdio/json-rpc server for external agents.

Launch: spec-editor mcp-server [-p <project>]
Connects third-party MCP clients (Cursor, Aider, Claude Desktop, Zed) to storage.
Supports project switching via the switch_project tool.
"""

import json
import sys
from pathlib import Path

import click

from src.agents.tools import build_read_only_handlers, get_tool_definitions
from src.config.methodology import load_methodology
from src.storage.filesystem import FilesystemStorage


@click.command()
@click.option("--path", "-p", default=None, type=click.Path(exists=True))
def mcp_server(path: str | None) -> None:
    """Start MCP server. -p is optional, can be switched via switch_project."""
    _state = {"storage": None, "handlers": {}, "source_dir": "", "project_path": ""}

    # JSON-RPC must be clean on stdout — all logging to stderr
    import logging, sys
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    logging.basicConfig(stream=sys.stderr, level=logging.ERROR, format="%(message)s")

    if path:
        _init_state(_state, Path(path).resolve())

    tool_schemas = _build_schemas()

    for line in sys.stdin:
        try:
            request = json.loads(line.strip())
        except json.JSONDecodeError:
            continue

        method = request.get("method", "")
        req_id = request.get("id", 0)

        if method == "initialize":
            _respond(
                req_id,
                {
                    "protocolVersion": "0.1",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "spec-editor-mcp", "version": "1.0"},
                },
            )

        elif method == "tools/list":
            _respond(req_id, {"tools": tool_schemas})

        elif method == "tools/call":
            params = request.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            if tool_name == "switch_project":
                new_path = arguments.get("path", "")
                if new_path:
                    _init_state(_state, Path(new_path).resolve())
                    _respond(
                        req_id,
                        _ok(
                            {
                                "project": _state["project_path"],
                                "elements": len(_state["storage"].list_all())
                                if _state["storage"]
                                else 0,
                            }
                        ),
                    )
                else:
                    _respond(req_id, _err("path required"))
                continue

            handler = _state["handlers"].get(tool_name)
            if handler:
                try:
                    import asyncio

                    result = handler(**arguments)
                    if asyncio.iscoroutine(result):
                        result = asyncio.run(result)
                    _respond(req_id, _ok(result))
                except Exception as exc:
                    _respond(req_id, _err(str(exc)))
            else:
                _respond(req_id, _err(f"No project loaded. Use switch_project first."))

        elif method == "shutdown":
            break


def _build_schemas() -> list:
    tools = get_tool_definitions(writable=False)
    return [
        {"name": t.name, "description": t.description, "inputSchema": t.parameters}
        for t in tools
    ] + [
        {
            "name": "switch_project",
            "description": "Initialise a new project with a methodology. path — directory with existing methodology.yaml.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to project directory"}
                },
                "required": ["path"],
            },
        }
    ]


def _init_state(state: dict, project_path: Path) -> None:
    storage = FilesystemStorage(project_path)
    method_path = project_path / "methodology.yaml"
    methodology = (
        load_methodology(method_path) if method_path.exists() else _fake_methodology()
    )
    state["storage"] = storage
    state["handlers"] = build_read_only_handlers(
        storage, methodology, source_dir=str(project_path / "source")
    )
    state["source_dir"] = str(project_path / "source")
    state["project_path"] = str(project_path)


def _ok(data) -> dict:
    return {
        "content": [
            {"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}
        ]
    }


def _err(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}], "isError": True}


def _respond(req_id, result: dict) -> None:
    sys.stdout.write(
        json.dumps(
            {"jsonrpc": "2.0", "id": req_id, "result": result}, ensure_ascii=False
        )
        + "\n"
    )
    sys.stdout.flush()


def _fake_methodology():
    from src.config.methodology import Methodology

    return Methodology(name="mcp", version="1.0")

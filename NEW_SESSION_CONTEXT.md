# Context for new Copilot session — `search_symbol` MCP tool

## Goal
Verify that the `search_symbol` MCP tool is available in Copilot's tool palette and works correctly.

## What is `search_symbol`
A new MCP tool added to `src/agents/tools_code.py` that searches for code symbols (classes, functions, methods) by name across Python, TypeScript, Go, Java, and Rust files. Returns symbol name, kind, file path, line number, decorators, and docstring.

## Current state
- **Code**: Fully implemented in `src/agents/tools_code.py` (line 311), registered in `CODE_RO_TOOLS` (line 535), and in `add_code_tools_handlers` (line 591).
- **stdio MCP**: Running (PID visible via `ps aux | grep "spec-editor mcp"`), discovered **49 tools** including `search_symbol` (verified in MCP logs at `~/Library/Application Support/Code/logs/`).
- **HTTP MCP** (port 8088): Works correctly, returns results for `search_symbol` queries.
- **User-level MCP**: Cleared (`~/Library/Application Support/Code/User/mcp.json` has empty servers).
- **Workspace MCP**: `.vscode/mcp.json` has `specEditor` stdio server pointing to `.venv/bin/spec-editor mcp -p ${workspaceFolder}`.

## The problem
Previous Copilot chat session (started before `search_symbol` was added) cached only ~17 tools. The tool list is session-locked — VS Code reloads don't refresh it. A fresh Copilot session should see all 49 tools.

## How to verify in new session
Try calling:
```
Tool: mcp_spec-editor-m_search_symbol
Args:
  code_dir: "/Users/dmitry/Documents/Droid/spec-editor2"
  query: "AgentWorker"
  project_path: "/Users/dmitry/Documents/Droid/spec-editor2"
```

Expected result: 1 symbol found — `AgentWorker` (class) at `src/agents/persistent_agent.py:26`, 247 files scanned.

## Quick diagnostic commands
```bash
# Check stdio MCP process
ps aux | grep "spec-editor mcp" | grep -v grep

# Check MCP logs
LATEST=$(ls -t ~/Library/Application\ Support/Code/logs/ | head -1)
tail -5 "$HOME/Library/Application Support/Code/logs/$LATEST/window1/mcpServer.mcp.config.ws0.specEditor.log"

# Test HTTP MCP directly
curl -s -X POST http://127.0.0.1:8088/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search_symbol","arguments":{"code_dir":"/Users/dmitry/Documents/Droid/spec-editor2","query":"AgentWorker","project_path":"/Users/dmitry/Documents/Droid/spec-editor2"}}}'

# Test stdio MCP directly
cd /Users/dmitry/Documents/Droid/spec-editor2 && (echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'; sleep 1) | .venv/bin/spec-editor mcp -p . 2>/dev/null | tail -1 | .venv/bin/python -c "
import sys,json
d=json.load(sys.stdin)
tools=[t['name'] for t in d['result']['tools']]
print(f'{len(tools)} tools')
print('search_symbol present:', 'search_symbol' in tools)
"
```

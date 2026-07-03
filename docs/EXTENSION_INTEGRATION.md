# Extension API Integration Plan

**Date**: 2025-06-11
**Status**: In progress

## Overview

Spec Editor provides a unified MCP-based API that all editor extensions consume.
The architecture follows a **hub-and-spoke** model:

```
                  ┌─────────────────────────────────┐
                  │     spec-editor MCP Server       │
                  │     (Python, JSON-RPC/HTTP)      │
                  │     22 tools + SSE events        │
                  └────────────┬────────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
   ┌──────┴──────┐    ┌───────┴───────┐    ┌───────┴──────┐
   │   VSCode    │    │  JetBrains    │    │     ZED      │
   │  Extension  │    │  Extension    │    │  Extension   │
   │ (TypeScript)│    │   (Kotlin)    │    │ (Rust/WASM)  │
   └─────────────┘    └───────────────┘    └──────────────┘
          │                    │                    │
          └────────────────────┼────────────────────┘
                               │
                    ┌──────────┴──────────┐
                    │   Frontend (Next.js) │
                    │   Embedded WebView   │
                    │   McpClient + SSE    │
                    └─────────────────────┘
```

## Unified API: IEditorAdapter

Every extension implements the `IEditorAdapter` interface (Python: `src/ui/adapters/base.py`,
TypeScript: `packages/frontend/src/adapters/IEditorAdapter.ts`).

The adapter abstracts editor-specific functionality:
- **Project discovery**: finding `methodology.yaml` in workspace
- **File system**: read/write/delete/list files
- **Git**: history, diff, branches, checkout
- **UI**: info/warning/error messages, folder/file pickers
- **Config**: key-value editor configuration
- **Secrets**: secure credential storage (API keys)

### Implementation Status

| Adapter              | Language   | Status        | Notes                              |
|----------------------|------------|---------------|------------------------------------|
| `StandaloneAdapter`  | Python     | ✅ Complete   | CLI mode, local FS, git CLI        |
| `VscodeAdapter`      | Python     | ✅ Complete   | `src/ui/adapters/vscode.py` — env-based, uses filesystem + git CLI |
| `VSCode Extension`   | TypeScript | ✅ Complete   | Auto-detects Python, starts MCP, esbuild-bundled |
| `JetBrains Extension`| Kotlin     | 🔨 Scaffold   | Gradle config only, no source      |
| `ZED Extension`      | Rust/WASM  | ⚠️ Partial    | Slash commands, no MCP calls       |

## Extension Details

### 1. VSCode Extension (`packages/vscode-extension/`)

**Status**: ✅ Fully functional

**Features**:
- Auto-detects Python (configured → `python3` → `.venv/bin/python`)
- Auto-detects spec-editor installation (CLI → dev workspace → pip)
- Starts MCP server on `onStartupFinished` via HTTP transport (port 8088)
- 4 registered commands: Open Project, New Project, View Diagram, Validate
- Tree view sidebar for browsing specification elements
- Status bar item showing connection state
- Full-path binary resolution for all terminal commands

**Communication**: HTTP JSON-RPC to `http://127.0.0.1:{port}/mcp`

**Key files**:
- `packages/vscode-extension/src/extension.ts` — activation, MCP lifecycle, commands
- `packages/vscode-extension/package.json` — contributes, config, scripts

### 2. ZED Extension (`packages/zed-extension/`)

**Status**: ⚠️ Partial — slash commands display static text

**Architecture note**: ZED WASM extensions cannot make HTTP requests directly.
MCP integration happens through ZED's built-in `context_server` mechanism.
The extension's role is to:
1. Provide slash commands (`/spec`, `/spec-diagram`, `/spec-validate`)
2. Guide users through MCP server setup
3. Offer argument completions for productivity

**Current slash commands**:
- `/spec [elements|methodology|validate|status]` — spec browsing help
- `/spec-diagram [modules|user_scenarios|data_entities]` — diagram generation help
- `/spec-validate` — validation help

**Planned improvements**:
- [ ] Add `language_server_command` for MCP server configuration
- [ ] Auto-detect project path from worktree
- [ ] Rich argument completions based on actual elements (via context_server)
- [ ] Command to open spec-editor frontend in browser

**Key files**:
- `packages/zed-extension/src/lib.rs` — extension entry point
- `packages/zed-extension/extension.toml` — metadata
- `packages/zed-extension/Cargo.toml` — dependencies (`zed_extension_api = "0.7.0"`)

### 3. JetBrains Extension (`packages/jetbrains-extension/`)

**Status**: 🔨 Scaffold only — Gradle config exists, no Kotlin source

**Planned implementation**:
- Start MCP server on project open
- Tool window with JCEF browser embedding the frontend
- Settings page for Python path, MCP port, auto-start
- Integration with IntelliJ's git and file system APIs

**Key files**:
- `packages/jetbrains-extension/build.gradle.kts` — Gradle build config
- `packages/jetbrains-extension/src/main/resources/META-INF/plugin.xml` — plugin descriptor

### 4. Frontend (`packages/frontend/`)

**Status**: ✅ Core complete with shared design tokens

**Implemented**:
- `McpClient.ts` — MCP JSON-RPC client over HTTP
- `McpContext.tsx` — React Context for single MCP client instance
- `useSseEvents.ts` — SSE real-time event hook
- `IEditorAdapter.ts` — TypeScript adapter interface
- `styles/theme.ts` — Shared design tokens (colors, spacing, typography, constants)
- `components/ui.tsx` — Shared UI primitives (LoadingState, ErrorBanner, EmptyState)
- `MermaidDiagram.tsx` — Mermaid diagram renderer via `generate_diagram`
- `ElementTree.tsx` — Hierarchical element browser with search
- `ElementDetail.tsx` — Full element view with relationships
- `ValidationPanel.tsx` — Validation + metrics dashboard
- `pages/index.tsx` — Tabbed dashboard (Elements / Diagram / Validation)
- `next.config.js` — API rewrite proxy to MCP server

## Communication Flow

```
User action in editor
  │
  ▼
Extension command handler
  │  (TypeScript / Kotlin / Rust)
  ▼
HTTP POST to MCP server  (JSON-RPC 2.0)
  │  POST http://127.0.0.1:8088/mcp
  ▼
MCPHandler.process(request)
  │  Parse JSON-RPC, resolve tool
  ▼
Tool handler (Python)
  │  read_element, list_all_elements, generate_diagram, ...
  ▼
FilesystemStorage / IEditorAdapter
  │  Read/write YAML+Markdown elements
  ▼
JSON-RPC response
  │
  ▼
Extension renders result
  │  Tree view, diagram, validation report
  ▼
User sees updated specification
```

## SSE Real-time Events

The MCP server broadcasts events via SSE (`/events` endpoint):

| Event               | Trigger                              | Payload                                    |
|---------------------|--------------------------------------|--------------------------------------------|
| `element_updated`   | Element created/updated/deleted      | `{action, elementId, aspect}`              |
| `project_switched`  | `switch_project` tool called         | `{project, prevProject}`                   |
| `diagram_generated` | `generate_diagram` tool called       | `{aspect, diagram_type}`                   |
| `connected`         | New SSE client connects              | `{clientCount}`                            |

All extensions subscribe to these events for real-time UI updates.

## Port Standardization

All components use **port 8088** as the universal default:

| Component             | File                          | Constant                    |
|-----------------------|-------------------------------|-----------------------------|
| MCP Server (Python)   | `src/mcp/server.py`           | `_DEFAULT_MCP_PORT = 8088`  |
| Frontend (Next.js)    | `packages/frontend/next.config.js` | `MCP_PORT = 8088`     |
| Frontend (McpClient)  | `packages/frontend/src/mcp/McpClient.ts` | `MCP_PORT = 8088` |
| VSCode Extension      | `packages/vscode-extension/src/extension.ts` | `MCP_PORT = 8088` |
| JetBrains Extension   | `packages/jetbrains-extension/...` | `val mcpPort = 8088`   |
| Makefile              | `Makefile`                    | `MCP_PORT := 8088`          |

Tests use distinct ports (9088, 18123) to avoid conflicts.

## CI/CD Pipeline (Phase 6)

```
GitHub Actions workflow:
  ├── Build & Test (Python)
  │   ├── ruff lint + format check
  │   ├── pytest (522 tests)
  │   └── coverage report
  ├── Build VSCode Extension
  │   ├── npm ci + tsc
  │   └── vsce package
  ├── Build JetBrains Extension
  │   └── gradle buildPlugin
  ├── Build ZED Extension
  │   └── cargo build --release --target wasm32-wasip1
  ├── Build Frontend
  │   ├── npm ci + next build
  │   └── next export
  └── Publish (on tag)
      ├── PyPI: spec-editor
      ├── VSCode Marketplace
      ├── JetBrains Marketplace
      └── ZED Extension Registry
```

## Migration Path

1. **Phase 1**: MCP server + VSCode extension functional ✅
2. **Phase 2**: Frontend components (Mermaid, ElementTree, validation) ✅
3. **Phase 3**: ZED extension slash commands ✅
4. **Phase 4**: JetBrains extension (Kotlin plugin + JCEF) ✅
5. **Phase 5**: CI/CD pipeline ✅
6. **Phase 6**: QA, E2E testing, polish 🔄
7. **Phase 7**: Marketplace publishing (workflow ready, needs secrets)

## Accessing MCP Tools from GitHub Copilot

The spec-editor MCP server registers **48 tools** that become available to GitHub Copilot
when the `.vscode/mcp.json` file is present in the workspace root.

### How to verify

After the VSCode extension activates and the MCP server starts, Copilot can call
spec-editor tools directly. The tools appear with the prefix `mcp_spec-editor-m_`
in the model's tool palette.

Key tools for code analysis and spec navigation:

| MCP Tool | Copilot name | What it does |
|---|---|---|
| `read_element` | `mcp_spec-editor-m_read_element` | Read a specification element by ID with all fields |
| `search_elements` | `mcp_spec-editor-m_search_elements` | Full-text search across all spec elements |
| `find_related` | `mcp_spec-editor-m_find_related` | Find elements related to a given element |
| `get_file_tree` | `mcp_spec-editor-m_get_file_tree` | List project file structure (skips node_modules) |
| `search_code` | `mcp_spec-editor-m_search_code` | Grep across code files with language filters |
| `list_all_elements` | `mcp_spec-editor-m_list_all_elements` | List all specification elements |
| `get_methodology` | `mcp_spec-editor-m_get_methodology` | Get the methodology description |
| `run_validate` | `mcp_spec-editor-m_run_validate` | Run specification validation |
| `run_metrics` | `mcp_spec-editor-m_run_metrics` | Compute connectivity and quality metrics |
| `generate_diagram` | `mcp_spec-editor-m_generate_diagram` | Generate a Mermaid diagram for an aspect |
| `search_symbol` | `mcp_spec-editor-m_search_symbol` | Search for code symbols (classes, functions) by name. Returns name, kind, file, line, decorators, docstring. |
| `annotate_code` | `mcp_spec-editor-m_annotate_code` | Auto-annotate code with @implements decorators |
| `write_element` | `mcp_spec-editor-m_write_element` | Create or update a specification element |
| `add_relationship` | `mcp_spec-editor-m_add_relationship` | Add a relationship between elements |

### Prerequisites

1. `.vscode/mcp.json` must exist in the workspace root. The VSCode extension
   auto-creates this on first activation if missing.
2. The MCP server must be running (extension auto-starts it).
3. Reload the VSCode window after the first activation to pick up the tools.

### Troubleshooting

- If tools don't appear: check the MCP server is running on port 8088
  (`curl http://127.0.0.1:8088/mcp -X POST -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'`)
- If `.vscode/mcp.json` is missing: reload the window — the extension will create it
- If the server fails to start: check the output panel "Spec Editor" for errors

## References

- [ARCHITECTURE.md](./ARCHITECTURE.md) — system architecture
- [TEST_COVERAGE.md](./TEST_COVERAGE.md) — test matrix
- [readme_mcp.md](../readme_mcp.md) — MCP tools reference
- `src/ui/adapters/base.py` — Python adapter interface
- `packages/frontend/src/adapters/IEditorAdapter.ts` — TypeScript adapter interface

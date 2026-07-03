# Spec Editor MCP — Tools for External AI Agents

Connect any MCP-compatible agent (Zed, Claude Code, Cursor, Aider) to
your requirements specification.

> For convenience, the MCP server is named `spec-editor-mcp` in the project and
> the same container name is used in Docker.
> In `docker-compose.yml`, the MCP service defines `container_name: spec-editor-mcp`.

## Quick Start

### 1. Connect to Zed

Add to `~/.config/zed/settings.json`:

```json
{
  "mcp_servers": {
    "spec-editor": {
      "command": "/path/to/.venv/bin/spec-editor",
      "args": ["mcp", "-p", "/path/to/project"]
    }
  }
}
```

### 2. Run standalone

```bash
spec-editor mcp -p /path/to/project
```

Listens on stdin/stdout in JSON-RPC 2.0 format.

## Available Tools (19 tools)

### Specification Read

| Tool | Description |
|------|-------------|
| `read_element` | Read full element by ID (aspect, type, title, status, content, relationships) |
| `list_aspect` | List all elements in an aspect (e.g., "modules", "user_scenarios") |
| `list_all_elements` | List all elements in the project (summary form) |
| `search_elements` | Full-text search by ID, title, and content |
| `find_related` | Find all elements linked to a given element |
| `get_methodology` | Get methodology description (aspects, element types, relationship types) |
| `run_validate` | Run MCP validation — checks YAML, references, statuses |
| `run_metrics` | Compute connectivity metrics (coverage, orphans, cross-aspect links) |

### Source Documents

| Tool | Description |
|------|-------------|
| `read_source` | Read source documents from `source/` folder. Without args — list files. With filename — read content |
| `export_srs` | Export specification as SRS document (IEEE 830 format) |

### Code Verification

| Tool | Description |
|------|-------------|
| `search_code` | Grep search in code directory |
| `get_file_tree` | Show project file structure |
| `run_shell` | Execute shell command (tests, linters, builds) |
| `read_lints` | Run linter (ruff) on code |
| `verify_implements` | Verify that a Python file implements requirements via `@implements("REQ-ID")` |
| `verify_traceability` | Full-project traceability check — coverage %, gaps, missing implementations |
| `annotate_code` | Auto-annotate legacy code with `@implements` based on symbol names |

### Questions

| Tool | Description |
|------|-------------|
| `list_questions` | Read open questions from `questions.jsonl` |

### Project Switching

| Tool | Description |
|------|-------------|
| `switch_project` | Switch to a different project by path at runtime |

## Bidirectional Traceability

```
Specification            Code
─────────────           ──────
ent-category   ←──→    @implements("ent-category")
(Category)             class Category(Base): ...
```

Use `verify_traceability` to check coverage and find gaps.

## Code Generation Workflow

```
1. read_element("ent-category")     → understand requirement
2. Write code with @implements      → create implementation
3. verify_implements("models/cat.py") → validate single file
4. verify_traceability              → check full coverage
```

## Ingestion Pipeline

```
Telegram chat → sources_raw/msg_*.md
  → preprocessor (spam filter + fact extraction)
  → analyzer (duplicate/conflict detection)
  → SRC draft elements in aspects/sources/
  → agents create spec elements with derived_from
```

## Example: Zed Agent Session

```
Agent: list_aspect("modules")
MCP:   { "aspect": "modules", "count": 14, "elements": [...] }

Agent: read_element("MOD-007")
MCP:   { "id": "MOD-007", "title": "Ingestion Pipeline", ... }

Agent: write_code(models/category.py, ...)
       # writes: @implements("ent-category")\nclass Category(Base): ...

Agent: verify_implements(code_dir=".", file_path="models/category.py")
MCP:   { "passed": true, "implemented": 1, "gaps": [] }
```

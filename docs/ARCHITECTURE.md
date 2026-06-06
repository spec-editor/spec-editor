# Architecture

Spec Editor turns requirements documents into structured specifications
using multiple AI agents in debate, then verifies code implements them.

## Pipeline

```
sources_raw/*    →  [ingestion]  →  source/*.md   →  [spec-editor run]  →  aspects/*.md
(PDF, chat logs)     (auto)          (cleaned)          (agents debate)      (structured spec)
```

Ingestion runs automatically on `spec-editor run`:
1. Scans `sources_raw/` for new files (PDF, TXT, Telegram messages)
2. Classifies requirement vs spam (LLM batch)
3. Extracts structured facts → `source/filtered_*.md`
4. Creates SRC elements in `aspects/sources/` for traceability

## Components

### Agent Dialogue

Two agents + orchestrator work in structured rounds:

- **Agent 1 & Agent 2** — work on all methodology aspects collaboratively
- **Orchestrator** — monitors dialogue, resolves conflicts, decides completion

Agents can spawn **skill-based helpers** for specialised tasks:
`scenario_decomposer`, `ui_navigator`, `metrics_linker`, `traceability_checker`.
Each skill has a focused prompt and toolset defined in `skills.yaml`.

Agents use **blind voting** (4 strategies: consensus, majority, weighted, debate)
to resolve disagreements without anchoring bias.

### Language Support

Prompts auto-detect source document language (Cyrillic → Russian, etc.)
and switch to the appropriate language pack. Available languages:
EN, RU, ES, FR, DE. Customise via `SPEC_EDITOR__PROMPT_LANGUAGE` env var.

### Storage

Elements are stored as Markdown files with YAML frontmatter:

```markdown
---
id: MOD-001
aspect: modules
element_type: module
title: Админ-панель (Core Admin)
status: draft
derived_from: [SRC-001]
relationships:
  depends_on: [{role: relates_to, target: MOD-007}]
---
Центральная админ-панель управления сетью сайтов...
```

Git-native: diff, merge, blame, history.

### Code Traceability

Annotate your source code with `@implements`:

```python
# @implements("MOD-001")
class AuthService:
    def login(self, email, password): ...
```

Supported languages: Python, TypeScript, JavaScript, Go, Java, Kotlin, Rust.

Run `spec-editor verify-traceability` to check coverage.

### MCP Server

22 tools exposed via Model Context Protocol. External AI agents (Claude Code, Zed, Cursor)
can read your specification:

```bash
spec-editor mcp
```

Key tools: `get_context_for_file`, `search_elements`, `read_element`, `list_all_elements`,
`run_validate`, `run_metrics`, `find_related`, `get_file_tree`.

### Methodologies

Specifications follow a YAML-defined methodology — a structure of aspects,
element types, and relationships. Built-in:

| Methodology | Description |
|-------------|-------------|
| `waterfall` | Full spec: 8 aspects (sources, modules, scenarios, UI, entities, NFR, implementation, metrics) |
| `waterfall-ru` | Russian-localised waterfall methodology |
| `agile` | Sprint backlog: epics → user stories → acceptance criteria |
| `scrum` | Agile + sprints (goal, capacity, velocity, DoD) |
| `kanban` | Agile + workflow stages (WIP limits, cycle time) |
| `api-first` | OpenAPI 3.0 contracts |

Create custom methodologies in YAML — see `methodologies/waterfall.yaml` for the format.

### Export Formats

```bash
spec-editor export -p .              # SRS document (Markdown)
spec-editor export -p . -f trlc      # TRLC (BMW-compatible)
spec-editor export -p . -f openapi   # OpenAPI 3.0
spec-editor export -p . -f jira      # Jira CSV import
```

## CLI Reference

| Command | What it does |
|---------|-------------|
| `spec-editor init` | Create new project |
| `spec-editor run` | Launch agent dialogue |
| `spec-editor run -r N` | Run exactly N rounds |
| `spec-editor demo` | Quick demo (no LLM needed) |
| `spec-editor view` | Interactive Mermaid graph |
| `spec-editor status` | Element and metric summary |
| `spec-editor validate` | Check reference integrity |
| `spec-editor log` | View agent dialogue log |
| `spec-editor export` | Export to SRS/TRLC/OpenAPI/Jira |
| `spec-editor codegen` | Generate code skeletons from spec |
| `spec-editor analyze` | Analyze new requirements for duplicates |
| `spec-editor mcp` | Start MCP server |
| `spec-editor decisions` | View architecture decisions |

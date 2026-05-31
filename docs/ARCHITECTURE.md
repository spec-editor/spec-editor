# Architecture

Spec Editor turns requirements documents into structured specifications
using multiple AI agents in debate, then verifies code implements them.

## Pipeline

```
source/*.md    →  [spec-editor run]  →  aspects/*.md   →  [verify]  →  Coverage
(raw reqs)         (agents debate)      (structured)       (@implements)   Report
```

## Components

### Agent Dialogue

Two specialist agents + one orchestrator work in structured rounds:

- **Agent 1** — focuses on modules, data entities, implementation
- **Agent 2** — focuses on user scenarios, UI, NFRs
- **Orchestrator** — monitors dialogue, resolves conflicts, decides completion

Agents can use **blind voting** (4 strategies: consensus, majority, weighted, debate)
to resolve disagreements without anchoring bias.

### Storage

Elements are stored as Markdown files with YAML frontmatter:

```markdown
---
id: MOD-001
aspect: modules
element_type: module
title: User Authentication
status: reviewed
relates_to: [MOD-003]
---
Handles user registration, login, password reset.
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

19 tools exposed via Model Context Protocol. External AI agents (Claude Code, Zed, Cursor)
can read and write your specification:

```bash
spec-editor mcp
```

### Methodologies

Specifications follow a YAML-defined methodology — a structure of aspects,
element types, and relationships. Built-in:

| Methodology | Description |
|-------------|-------------|
| `waterfall` | Full spec: 7 aspects (modules, scenarios, UI, entities, NFR, implementation, metrics) |
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
| `spec-editor demo` | Quick demo (no LLM needed) |
| `spec-editor view` | Interactive Mermaid graph |
| `spec-editor status` | Element and metric summary |
| `spec-editor validate` | Check reference integrity |
| `spec-editor annotate` | Add @implements to code |
| `spec-editor verify-traceability` | Coverage report |
| `spec-editor export` | Export to SRS/TRLC/OpenAPI/Jira |
| `spec-editor codegen` | Generate code skeletons |
| `spec-editor mcp` | Start MCP server |
| `spec-editor decisions` | View architecture decisions |

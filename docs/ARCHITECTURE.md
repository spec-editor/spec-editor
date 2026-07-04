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
title: Authentication Service
status: draft
derived_from: [SRC-001]
relationships:
  depends_on: [{role: relates_to, target: MOD-007}]
---
Central authentication service handling user login, registration,
and session management with OAuth2/OIDC support...
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

20+ tools exposed via Model Context Protocol. External AI agents (Claude Code, Zed, Cursor)
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

## Pluggable Subsystem Architecture

All infrastructure subsystems follow the same pattern: **ABC → factory → `local.yaml` config**.
This allows swapping backends without changing agent code.

```
local.yaml          src/<subsystem>/__init__.py     Implementation
──────────          ──────────────────────────     ──────────────
secrets:            SecretProvider ABC              env, aws_secrets, vault, noop
events:             AbstractEventBus ABC            redis, memory, nats
notifications:      Notifier ABC                    log, slack, telegram, email
auth:               AuthProvider ABC                noop, casbin, openfga
templates:          TemplateEngine ABC              copier, jinja2, none
enforcement:        ArchEnforcer ABC                pytest, pytest_arch, import_linter, none
```

Each subsystem has a factory function (`create_*`) that reads `local.yaml`,
dispatches on `backend:` key, and falls back to a sensible local-first default.

### Secrets Provider (`src/secrets/`)

Resolves API keys, passwords, and tokens. Backends: `env`, `aws_secrets`, `vault`, `noop`.

```yaml
secrets:
  backend: env
```

### Event Bus (`src/events/`)

Inter-component pub/sub messaging. Backends: `redis`, `memory`, `nats`.
Used by storage layer to publish `elements:changed` events.

### Notifier (`src/notifiers/`)

Sends alerts on lifecycle events. Backends: `log` (stderr), `slack`, `telegram`, `email`.

### Auth Provider (`src/auth/`)

Access control for multi-user/enterprise. Backends: `noop` (allow all), `casbin` (RBAC), `openfga` (ReBAC).

## Implementation Framework

A structured, 3-layer code generation pipeline that constrains LLM output
to follow declared architectural patterns.

```
Spec Element        Layer 1: Pattern          Layer 2: Templates       Layer 3: Enforcement
───────────         ─────────────────         ──────────────────       ───────────────────
MOD-001 (reviewed)  hexagonal / ddd / clean   copier / jinja2 / none   pytest / pytest_arch
    │                       │                        │                        │
    ▼                       ▼                        ▼                        ▼
Architect decides    Directory structure     File skeletons with       Auto-generated
IMP-001 plan         + dependency rules      naming conventions        arch test file
```

### Layer 1 — Architectural Pattern (`src/implementation/patterns/`)

Defines structural rules: directory layout, dependency direction, naming conventions.
Patterns: `hexagonal`, `clean`, `ddd`, `mvc`, `none`. Declared in `methodology.yaml`:

```yaml
implementation:
  pattern: hexagonal
  language: python
```

### Layer 2 — Coding Templates (`src/implementation/templates/`)

Generates code skeletons from Jinja2 or Copier templates. Built-in templates:
domain service, repository port, Postgres adapter (hexagonal pattern).

### Layer 3 — Architecture Enforcement (`src/implementation/enforcement/`)

Auto-generates `tests/arch/test_architecture.py` from dependency rules.
Runs as part of `run_tests` — no separate agent. Backends: `pytest`, `pytest_arch`, `import_linter`.

### Implementation Architect

A specialised skill that makes ONE-TIME implementation decisions BEFORE coding.
Creates IMP-* elements with `implementation_architect:` block:

```yaml
# IMP-001 (aspect: implementation, parent: MOD-001)
implementation_architect:
  structure: hexagonal
  domain_style: ddd
  template: grpc_service
  layer: domain
  ports: [repository, messaging]
  adapters: [postgres, kafka]
```

Decisions appear in the coding agent's context as immutable constraints.
Business requirements (MOD) stay clean; implementation plans (IMP) carry technical decisions.

### Cycle Loop (end-to-end)

```
INGEST → ANALYST → ARCHITECT → PM AGENT → (repeat until convergence)
logs      DRAFT→    IMP plans    codegen
→bugs     REVIEWED  created      →test→deploy
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

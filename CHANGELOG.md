# Changelog

All notable changes to Spec Editor will be documented in this file.

## [0.1.0] — Unreleased

### Added

- Multi-agent requirements engineering (Agent 1, Agent 2, Orchestrator)
- Methodology-aware specification (waterfall: 7 aspects, 18 element types)
- YAML-frontmatter storage with markdown content
- Bidirectional code traceability via `@implements` decorator
- 19 MCP tools for external AI agents (Zed, Claude Code, Cursor)
- CLI: `init`, `run`, `validate`, `status`, `export`, `analyze`, `questions`, `mcp`
- Ingestion pipeline: Telegram → preprocessor → analyzer → SRC elements
- TypeScript/JavaScript parser (tree-sitter AST)
- Python parser (stdlib `ast`)
- Word-level matching annotator for legacy code
- `verify_traceability` — full-project requirement coverage check
- `verify_implements` — single-file requirement verification
- `annotate_code` — auto-annotation of code with `@implements`
- Cost protection: per-element threshold, no-op detection, cumulative tracking
- Async question system (`questions.jsonl`)
- Batch classification (30 files/batch, ~40x API call reduction)
- Context compaction for long-running agent sessions
- SRC deletion protection with `_deleted/` archive
- Pluggable logging backend (`LogConfigBackend` ABC, `LocalYamlBackend`)
- Pluggable Secrets Provider (`env`, `aws_secrets`, `vault`, `noop`)
- Pluggable Event Bus (`redis`, `memory`, `nats`)
- Pluggable Notifier (`log`, `slack`, `telegram`, `email`)
- Pluggable Auth Provider (`noop`, `casbin`, `openfga`)
- Implementation Framework — 3-layer structured code generation:
  - Layer 1: Architectural Patterns (`hexagonal`, `clean`, `ddd`, `mvc`, `none`)
  - Layer 2: Coding Templates (Copier/Jinja2 with built-in hexagonal templates)
  - Layer 3: Architecture Enforcement (auto-generated `tests/arch/test_architecture.py`)
- `implementation_architect` skill — per-element implementation decisions stored on IMP-* elements
- Architect phase in cycle loop: INGEST → ANALYST → ARCHITECT → PM AGENT
- Element field `implementation_architect` for structured implementation decisions
- 154 tests (unit + integration)

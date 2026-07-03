# Changelog

All notable changes to Spec Editor will be documented in this file.

## [0.2.0] — 2026-07-04

### Added

- VS Code extension with MCP integration and WebView panel
- Frontend WebView (Next.js) with element tree, Mermaid diagrams, validation panel
- Supervisor graph for parallel multi-agent execution (LangGraph)
- Persistent agent workers with Redis task queues (coding, tester, devops, PM)
- Plugin system with setuptools entry points and dev-mode auto-discovery
- Pluggable backend architecture: Secrets (env, AWS, Vault), Auth (noop, Casbin, OpenFGA), Notifications (log, Slack, Telegram, email), Event Bus (Redis, NATS, memory)
- 7-language code traceability via `@implements` (Python, TypeScript, JavaScript, Go, Java, Kotlin, Rust)
- Code generation templates (SQLAlchemy, FastAPI, pytest, React, TypeScript interfaces)
- Architecture enforcement with auto-generated pytest tests
- Implementation framework with architectural patterns (hexagonal, clean, DDD, MVC)
- PM agent checks and automated code review
- Dry-run mode for safe agent experimentation
- Context compaction for long-running agent sessions
- SRC deletion protection with `_deleted/` archive
- Session manager with incremental runs and checkpoint resumption
- 345 tests (unit + integration)

### Changed

- Moved data files to `data/` package directory
- All LLM provider hardcodes replaced with env-var fallback chain
- Tree-sitter version constraints fixed

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

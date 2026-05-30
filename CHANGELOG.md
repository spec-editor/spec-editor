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
- 154 tests (unit + integration)

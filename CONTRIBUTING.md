# Contributing to Spec Editor

Thank you for your interest in contributing!

## Getting Started

```bash
git clone https://github.com/spec-editor/spec-editor.git
cd spec-editor
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest tests/ -v
```

Tests are organized by module:
- `tests/agents/` — agent framework
- `tests/ingestion/` — ingestion pipeline
- `tests/test_annotator.py` — code annotation
- `tests/test_traceability.py` — traceability verification

## Code Style

We use [ruff](https://github.com/astral-sh/ruff) for linting:

```bash
ruff check src/ tests/
ruff format src/ tests/
```

## Project Structure

```
src/
├── agents/          # Agent framework (base agent, orchestrator, dialogue)
├── cli/             # CLI commands (Click + Rich)
├── config/          # Settings, methodology loader
├── export/          # SRS export pipeline
├── ingestion/       # Telegram hook, preprocessor, analyzer
├── mcp/             # MCP server, validator, metrics, verifier
│   └── parsers/     # Python (stdlib ast) + TypeScript (tree-sitter)
├── providers/       # LLM abstraction (LiteLLM)
├── storage/         # File-system storage, models, adapter
└── tracing.py       # @implements decorator
```

## Adding a New Language Parser

1. Create `src/mcp/parsers/{language}.py`
2. Implement `parse_{language}(file_path) -> (list[CodeAnnotation], list[CodeSymbol])`
3. Register in `src/mcp/verifier.py` in `_get_parser()`
4. Add tests in `tests/`

## Pull Request Process

1. Fork the repository and create a feature branch
2. Add tests for new functionality
3. Run `pytest tests/ -v` — all tests must pass
4. Run `ruff check src/ tests/` — no lint errors
5. Open a PR against `main`

## License

By contributing, you agree that your contributions will be licensed
under the Apache License 2.0.
